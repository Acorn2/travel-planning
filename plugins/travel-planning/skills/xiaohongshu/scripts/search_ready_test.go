// Regression tests for the local search-wait repair (Apache-2.0).
package xiaohongshu

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/go-rod/rod/lib/proto"
)

func TestTravelSearchReadiness(t *testing.T) {
	bin := os.Getenv("TRAVEL_XHS_TEST_BROWSER")
	if bin == "" { t.Skip("set TRAVEL_XHS_TEST_BROWSER for browser fixture regression") }
	control, err := launcher.New().Bin(bin).Headless(true).Leakless(false).Launch()
	if err != nil { t.Fatal(err) }
	browser := rod.New().ControlURL(control)
	if err := browser.Connect(); err != nil { t.Fatal(err) }
	defer browser.Close()

	for _, test := range []struct { name, script, body, query, want string }{
		{"dynamic_dom_and_network", `window.__INITIAL_STATE__={search:{feeds:{value:[]}}};
		setInterval(()=>document.body.dataset.tick=Date.now(),10);
		setTimeout(()=>{window.__INITIAL_STATE__.search.feeds.value=[{id:'fixture-note',noteCard:{}}];
		document.body.insertAdjacentHTML('beforeend','<section class="note-item"><a href="/explore/fixture-note">note</a></section>')},100);`, `<img src="/slow.png">`, "test", ""},
		{"plain_array", `window.__INITIAL_STATE__={search:{feeds:[{id:'fixture-note',noteCard:{}}]}};`, `<section class="note-item"><a href="/explore/fixture-note">note</a></section>`, "test", ""},
		{"initial_empty_ref_is_not_done", `window.__INITIAL_STATE__={search:{feeds:{value:[]}}};`, "", "test", "超时"},
		{"homepage_state_is_not_search", `window.__INITIAL_STATE__={feed:{feeds:{value:[{id:'fixture-note'}]}}};`, "", "test", "超时"},
		{"wrong_keyword", `window.__INITIAL_STATE__={search:{feeds:{value:[{id:'fixture-note',noteCard:{}}]}}};`, `<section class="note-item"><a href="/explore/fixture-note">note</a></section>`, "other", "超时"},
		{"stale_state_without_matching_card", `window.__INITIAL_STATE__={search:{feeds:{value:[{id:'old',noteCard:{}}]}}};`, `<section class="note-item"><a href="/explore/new">note</a></section>`, "test", "超时"},
		{"confirmed_empty", `window.__INITIAL_STATE__={search:{feeds:{value:[]}}};`, `<div class="search-empty">暂无搜索结果</div>`, "test", ""},
		{"captcha", "", `<div class="captcha-modal">验证</div>`, "test", "verification_required"},
		{"login", "", `<div class="login-container">登录</div>`, "test", "login_required"},
	} {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
				if r.URL.Path == "/slow.png" { <-r.Context().Done(); return }
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				fmt.Fprintf(w,"<html><body>%s<script>%s</script></body></html>",test.body,test.script)
			}))
			defer server.Close()
			page, err := browser.Page(proto.TargetCreateTarget{})
			if err != nil { t.Fatal(err) }
			defer page.Close()
			if err := navigateSearch(page,server.URL+"/search_result?keyword="+test.query); err != nil { t.Fatal(err) }
			err = waitSearchReady(page,"test",900*time.Millisecond)
			if test.want == "" && err != nil { t.Fatal(err) }
			if test.want != "" && (err == nil || !strings.Contains(err.Error(),test.want)) { t.Fatalf("wanted %q, got %v",test.want,err) }
		})
	}
	page, err := browser.Page(proto.TargetCreateTarget{})
	if err != nil { t.Fatal(err) }
	defer page.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := waitSearchReady(page.Context(ctx),"test",time.Second); !errors.Is(err,context.Canceled) { t.Fatalf("cancellation lost: %v",err) }
}
