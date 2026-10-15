// Local modification of xpzouying/xiaohongshu-mcp (Apache-2.0).
// Wait for this query's rendered search data, not a quiet network or DOM.
package xiaohongshu

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/go-rod/rod"
)

const searchReadyJS = `(keyword) => {
	const visible = el => !!el && el.getClientRects().length > 0;
	if ([...document.querySelectorAll('[class*="captcha"], #captcha, .verify-modal')].some(visible))
		return JSON.stringify({status: 'verification_required'});
	if ([...document.querySelectorAll('.login-container, .login-modal')].some(visible))
		return JSON.stringify({status: 'login_required'});
	if (location.pathname !== '/search_result' || new URL(location.href).searchParams.get('keyword') !== keyword)
		return JSON.stringify({status: 'waiting'});
	const f = window.__INITIAL_STATE__?.search?.feeds;
	const feeds = Array.isArray(f) ? f : (f?.value !== undefined ? f.value : f?._value);
	if (!Array.isArray(feeds)) return JSON.stringify({status: 'waiting'});
	const links = [...document.querySelectorAll('section.note-item a[href]')];
	const matched = feeds.some(f => f.id && f.noteCard && links.some(a => {
		try { return new URL(a.href).pathname.split('/').includes(f.id); } catch { return false; }
	}));
	if (matched) return JSON.stringify({status: 'ready'});
	// An initial empty Vue ref does not prove a completed, empty search.
	const empty = [...document.querySelectorAll('.search-empty, .empty-container, .empty')]
		.some(el => visible(el) && /暂无|没有|未找到|无结果/.test(el.textContent));
	return JSON.stringify({status: feeds.length === 0 && empty ? 'empty' : 'waiting'});
}`

func navigateSearch(page *rod.Page, target string) error {
	ctx, cancel := context.WithTimeout(page.GetContext(), 30*time.Second)
	defer cancel()
	// Navigate waits for the navigation response; lazy images need not finish.
	if err := page.Context(ctx).Navigate(target); err != nil {
		return fmt.Errorf("搜索页面导航失败: %w", err)
	}
	return nil
}

func waitSearchReady(page *rod.Page, keyword string, timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(page.GetContext(), timeout)
	defer cancel()
	page = page.Context(ctx)
	ticker := time.NewTicker(300 * time.Millisecond)
	defer ticker.Stop()
	for {
		result, err := page.Eval(searchReadyJS, keyword)
		if err != nil {
			if ctx.Err() != nil {
				return fmt.Errorf("等待搜索数据就绪超时或取消: %w", ctx.Err())
			}
			return fmt.Errorf("读取搜索页面状态失败: %w", err)
		}
		var state struct { Status string `json:"status"` }
		if err := json.Unmarshal([]byte(result.Value.Str()), &state); err != nil {
			return fmt.Errorf("搜索页面状态格式无效")
		}
		switch state.Status {
		case "ready", "empty":
			return nil
		case "login_required":
			return fmt.Errorf("login_required: 小红书登录失效，请重新登录")
		case "verification_required":
			return fmt.Errorf("verification_required: 小红书需要验证码或设备验证")
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("等待搜索数据就绪超时或取消: %w", ctx.Err())
		case <-ticker.C:
		}
	}
}
