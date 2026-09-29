"""정적 웹 UI(resources/static/index.html) 계약 검증.

브라우저 없이 확인 가능한 항목만 다룬다. 실제 렌더링·클릭 동작은 육안 확인 영역이다.
"""
import re

from src.web.app import STATIC_DIR
from src.web.dto import ChatRequest

INDEX_PATH = STATIC_DIR / "index.html"


def _html() -> str:
    return INDEX_PATH.read_text(encoding="utf-8")


def _script() -> str:
    """<script> 블록만 추출(CSS·마크업 오탐 방지)."""
    return "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", _html(), re.S))


def test_basic_document_structure():
    html = _html()
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert 'lang="ko"' in html
    assert 'charset="utf-8"' in html
    assert 'name="viewport"' in html
    assert "<title>KUDOS RAG Agent" in html


def test_no_external_resources():
    """외부 CDN·원격 리소스를 쓰지 않는다(사내 문서가 로컬 서버 밖으로 나가지 않도록)."""
    html = _html()
    assert not re.search(r"<script[^>]+src\s*=\s*[\"']https?://", html, re.I)
    assert not re.search(r"<link[^>]+href\s*=\s*[\"']https?://", html, re.I)
    assert not re.search(r"@import\s+url\(\s*[\"']?https?://", html, re.I)


def test_fetch_targets_are_relative_paths():
    calls = re.findall(r"fetch\(\s*[`'\"]([^`'\"]+)", _script())
    assert calls, "fetch 호출을 찾지 못했다"
    assert all(url.startswith("/") for url in calls), calls
    assert {"/check", "/v1/chat/stream"} <= set(calls)
    assert any(u.startswith("/v1/threads/") for u in calls)   # 대화 복원(agent-09)
    assert len(set(calls)) == 3


def test_sse_event_names_match_server_contract():
    """서버가 보내는 이벤트 이름만 다루고, 알 수 없는 이름을 기대하지 않는다."""
    handled = set(re.findall(r"event\s*===\s*'([a-z]+)'", _script()))
    server_events = {"search", "token", "done", "error"}
    assert handled <= server_events, f"서버 계약에 없는 이벤트 처리: {handled - server_events}"
    assert {"search", "token", "error"} <= handled


def test_request_body_keys_match_chat_request():
    """요청 body 키가 ChatRequest 필드와 일치한다."""
    body = re.search(r"JSON\.stringify\(\s*\{([^}]*)\}", _script()).group(1)
    keys = set(re.findall(r"(\w+)\s*[:,}]", body)) | set(re.findall(r"\b(\w+)\s*$", body.strip()))
    for field in ChatRequest.model_fields:
        assert field in body, f"요청 body 에 {field} 가 없다"
    assert "thread_id: threadId" in body and "message" in keys


def test_thread_id_is_generated_in_browser():
    script = _script()
    assert script.count("crypto.randomUUID()") >= 2  # 최초 1회 + 새 대화
    # 저장된 대화가 있으면 그 ID 를 이어받고, 없을 때만 새로 만든다(agent-09)
    assert re.search(r"let\s+threadId\s*=\s*localStorage\.getItem\('kudos\.threadId'\)\s*\|\|\s*crypto\.randomUUID\(\)",
                     script)
    assert "localStorage.setItem('kudos.threadId', threadId)" in script


def test_new_conversation_resets_thread_and_log():
    script = _script()
    listener = script[script.index("resetBtn.addEventListener"):]
    assert "threadId = crypto.randomUUID()" in listener
    assert re.search(r"log\.(textContent\s*=\s*''|replaceChildren\(\))", listener)


def test_no_source_select_in_ui():
    """소스 선택은 에이전트가 한다(agent-01 설계). UI 에 select 를 두지 않는다."""
    assert "<select" not in _html()


def test_server_data_is_not_inserted_via_innerhtml():
    """XSS 회피: 서버 데이터는 textContent 로만 삽입한다."""
    script = _script()
    assert "innerHTML" not in script
    assert "outerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "document.write" not in script
    assert "textContent" in script


def test_urls_are_linkified_with_dom_api():
    script = _script()
    body = script[script.index("function linkifyUrls("):script.index("function formatDetail(")]
    assert "createElement('a')" in body
    assert "createTextNode" in body
    assert "target = '_blank'" in body
    assert "rel = 'noopener'" in body
    assert "innerHTML" not in body


def test_search_event_renders_tool_and_query():
    script = _script()
    body = script[script.index("function addSearchLine("):script.index("function linkifyUrls(")]
    assert "[검색]" in body
    assert "data.tool" in body and "data.query" in body
    assert "'search'" in body or "= 'search'" in body


def test_stream_parsing_keeps_incomplete_frame_in_buffer():
    script = _script()
    assert "indexOf('\\n\\n')" in script or 'indexOf("\\n\\n")' in script
    assert "buffer" in script
    assert "getReader()" in script
    assert "TextDecoder" in script


def test_http_error_detail_handles_both_string_and_array():
    """422 의 detail 은 배열, 그 외는 문자열."""
    script = _script()
    assert "Array.isArray(detail)" in script
    assert re.search(r"detail\.map\(\s*d\s*=>\s*d\.msg\s*\)", script)


def test_input_is_re_enabled_regardless_of_outcome():
    script = _script()
    assert "finally" in script
    finally_block = script.split("finally", 1)[1]
    assert "disabled = false" in finally_block


def test_reset_button_is_locked_while_answering():
    """답변 도중 새 대화를 누르면 화면만 비고 입력은 잠긴 채 남으므로, 전송 중에는 새 대화도 잠근다."""
    submit = _script()[_script().index("form.addEventListener('submit'"):_script().index("resetBtn.addEventListener")]
    before, after = submit.split("try {", 1)
    assert "resetBtn.disabled = true" in before
    assert "resetBtn.disabled = false" in after.split("finally", 1)[1]


def test_fetch_failure_shows_error_message():
    """서버 다운 등으로 fetch 자체가 실패하면 catch 에서 오류 메시지를 표시한다."""
    script = _script()
    assert re.search(r"catch\s*\(\s*err\s*\)", script)
    assert "요청 실패" in script
    assert ".err" in _html()  # 빨간 표시 스타일


def test_health_failure_is_handled():
    script = _script()
    assert "상태 확인 실패" in script
    assert "h.rag" in script and "h.ollama" in script and "h.status" in script


def test_answer_preserves_newlines_via_pre_wrap():
    assert "pre-wrap" in _html()


# --- agent-03: 답변 마크다운 렌더링 ---

def _function_body(name: str) -> str:
    """스크립트에서 함수 본문만 잘라낸다(다른 함수의 코드에 오탐하지 않도록)."""
    script = _script()
    start = script.index(f"function {name}(")
    depth = 0
    for i in range(script.index("{", start), len(script)):
        if script[i] == "{":
            depth += 1
        elif script[i] == "}":
            depth -= 1
            if depth == 0:
                return script[start:i + 1]
    raise AssertionError(f"{name} 본문을 찾지 못했다")


# V1. 렌더러 존재·안전

def test_markdown_renderer_exists_and_builds_dom_nodes():
    body = _function_body("renderMarkdown")
    assert "createElement" in body
    assert "createDocumentFragment()" in body


def test_markdown_renderer_never_uses_html_string_injection():
    """렌더러 본문에도 innerHTML 계열이 없어야 한다(XSS 표면 차단)."""
    for name in ("renderMarkdown", "renderInline", "renderList"):
        body = _function_body(name)
        assert "innerHTML" not in body
        assert "outerHTML" not in body
        assert "insertAdjacentHTML" not in body
        assert "document.write" not in body
    assert "createTextNode" in _function_body("renderInline")


# V2. 핸들러 연결

def test_token_handler_renders_markdown_from_accumulated_raw():
    script = _script()
    assert re.search(r"let\s+raw\s*=\s*''", script), "원문 누적 변수 raw 가 없다"
    branch = script[script.index("if (event === 'token')"):script.index("else if (event === 'search')")]
    assert "raw += data" in branch
    assert "renderMarkdown(raw)" in branch
    assert "replaceChildren(" in branch
    assert "textContent +=" not in branch  # 원문 기호를 그대로 붙이지 않는다


def test_error_event_still_appends_plain_text():
    branch = _script().split("else if (event === 'error')", 1)[1]
    assert "classList.add('err')" in branch
    assert "createTextNode(data)" in branch


# V3. 지원 문법·링크 안전

def test_renderer_creates_every_supported_block_element():
    script = _script()
    assert re.search(r"createElement\('h'\s*\+", script), "제목(h1~h4) 생성이 없다"
    for tag in ("ul", "ol", "li", "pre", "code", "strong", "p", "br"):
        assert re.search(rf"createElement\((?:'{tag}'|\w+ \? '{tag}'|.*'{tag}')", script), f"{tag} 생성이 없다"


def test_heading_level_is_limited_to_four():
    assert re.search(r"\^\(#\{1,4\}\)", _script()), "제목 정규식이 #{1,4} 가 아니다"


def test_markdown_link_href_must_be_http_scheme():
    """[텍스트](javascript:...) 같은 스킴은 링크로 만들지 않는다."""
    script = _script()
    assert r"\((https?:\/\/[^\s)]+)\)" in script
    hrefs = re.findall(r"\.href\s*=\s*(\S+?);", script)
    assert hrefs, "href 대입을 찾지 못했다"
    assert set(hrefs) <= {"m[0]", "m[4]"}  # 둘 다 https?:\/\/ 로 검증된 매치 그룹
    assert not re.search(r"['\"]javascript:", script)  # javascript: url 문자열 자체가 없다


def test_bare_url_regex_requires_http_scheme():
    assert r"/https?:\/\/[^\s)\]]+/g" in _script()


def test_code_block_content_is_inserted_verbatim():
    """코드 블록 안에서는 인라인 서식·링크를 적용하지 않는다."""
    body = _function_body("renderMarkdown")
    fence = body[body.index("RE_FENCE.test(line)"):body.index("const heading")]
    assert "createElement('pre')" in fence
    assert "createElement('code')" in fence
    assert "textContent = buf.join('\\n')" in fence
    assert "renderInline" not in fence


def test_renderer_splits_lines_and_uses_br_for_soft_breaks():
    """줄바꿈은 <br>/<p> 가 담당한다(pre-wrap 과 겹쳐 이중 줄바꿈이 나지 않도록)."""
    body = _function_body("renderMarkdown")
    assert "text.split('\\n')" in body
    assert "createElement('br')" in body


def test_inline_parser_handles_code_bold_and_link():
    body = _function_body("renderInline")
    assert "createElement('code')" in body
    assert "createElement('strong')" in body
    assert "createElement('a')" in body
    assert "linkifyUrls(parent)" in body  # 남은 맨 url 은 기존 함수가 처리


# --- agent-03 Validator 추가 검증 ---


def _style() -> str:
    """<style> 블록만 추출."""
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", _html(), re.S))


def test_rendered_block_elements_have_scoped_styles():
    """렌더된 블록 요소 스타일은 .msg 안으로 한정한다(헤더·폼 등 기존 UI 오염 방지)."""
    style = _style()
    for selector in (".msg h1", ".msg h2", ".msg h3", ".msg h4", ".msg p",
                     ".msg ul", ".msg ol", ".msg pre", ".msg code", ".msg pre code"):
        assert selector in style, f"{selector} 스타일이 없다"
    assert re.search(r"\.msg\s+pre\s*\{[^}]*background", style)
    assert re.search(r"\.msg\s+code\s*\{[^}]*font-family", style)
    assert re.search(r"\.msg\s+pre\s+code\s*\{[^}]*background\s*:\s*none", style)


def test_pre_wrap_is_kept_on_msg_container():
    """agent-02 계약 유지: .msg 의 white-space: pre-wrap 은 그대로 둔다."""
    assert re.search(r"\.msg\s*\{[^}]*white-space\s*:\s*pre-wrap", _style())


def test_done_event_only_marks_stream_ended():
    """done 은 마지막 재렌더 결과를 그대로 두고 정상 종결 표시만 한다(재렌더 없음)."""
    script = _script()
    branch = re.search(r"else if \(event === 'done'\)([^\n]*)", script).group(1)
    assert "ended = true" in branch
    assert "renderMarkdown" not in branch and "replaceChildren" not in branch


def test_search_event_discards_text_streamed_before_tool_call():
    """search 앞의 token(도구 호출 직전 멘트)은 최종 답변에 이어붙지 않도록 비운다."""
    script = _script()
    branch = script[script.index("else if (event === 'search')"):script.index("else if (event === 'error')")]
    assert "raw = ''" in branch
    assert "body.replaceChildren()" in branch
    assert "addSearchLine(wrap, data)" in branch


def test_stream_end_without_done_or_error_shows_interrupted():
    """done/error 없이 스트림이 끝나면 부분 답변만 남기지 않고 중단 안내를 붙인다."""
    script = _script()
    assert re.search(r"let\s+ended\s*=\s*false", script)
    error_branch = re.search(r"else if \(event === 'error'\)([^\n]*)", script).group(1)
    assert "ended = true" in error_branch
    assert re.search(r"if\s*\(\s*!ended\s*\)[^\n]*응답이 중단되었습니다", script)


# 아래는 실제 렌더 결과 DOM 을 확인한다(문자열 grep 으로는 ###·** 가 화면에 남는지 알 수 없다).
# index.html 의 렌더러 원본을 그대로 떼어 최소 DOM 셰임 위에서 node 로 실행한다.

_DOM_SHIM = """
export const Node = { TEXT_NODE: 3, ELEMENT_NODE: 1, FRAGMENT_NODE: 11 };
class B {
  constructor(t) { this.nodeType = t; this.childNodes = []; }
  appendChild(n) {
    if (n.nodeType === Node.FRAGMENT_NODE) { for (const c of [...n.childNodes]) this.appendChild(c); n.childNodes = []; return n; }
    n.parentNode = this; this.childNodes.push(n); return n;
  }
  replaceChild(newN, oldN) {
    const i = this.childNodes.indexOf(oldN);
    if (i < 0) throw new Error('replaceChild: not a child');
    const repl = newN.nodeType === Node.FRAGMENT_NODE ? [...newN.childNodes] : [newN];
    for (const c of repl) c.parentNode = this;
    this.childNodes.splice(i, 1, ...repl);
    if (newN.nodeType === Node.FRAGMENT_NODE) newN.childNodes = [];
    return oldN;
  }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  // 아래 3개는 agent-05 의 ask() 실행용(렌더러는 쓰지 않는다)
  insertBefore(n, ref) {
    if (ref === null) return this.appendChild(n);
    const i = this.childNodes.indexOf(ref);
    if (i < 0) throw new Error('insertBefore: ref not a child');
    n.parentNode = this; this.childNodes.splice(i, 0, n); return n;
  }
  replaceChildren(...nodes) { this.childNodes = []; for (const n of nodes) this.appendChild(n); }
  scrollIntoView() {}
}
class T extends B {
  constructor(v) { super(Node.TEXT_NODE); this._v = String(v); }
  get nodeValue() { return this._v; }
  set nodeValue(v) { this._v = String(v); }
  get textContent() { return this._v; }
}
class E extends B {
  constructor(tag) {
    // 브라우저의 Element.tagName 은 HTML 문서에서 항상 대문자다(DOM 표준)
    super(Node.ELEMENT_NODE); this.tagName = String(tag).toUpperCase(); this.attrs = {};
    this.classes = new Set();
    // agent-15: 스트리밍 커서 토글용으로 remove 를 추가(기존 add/contains 는 그대로)
    this.classList = {
      add: (c) => this.classes.add(c),
      remove: (c) => this.classes.delete(c),
      contains: (c) => this.classes.has(c),
    };
  }
  set textContent(v) { const t = new T(v); t.parentNode = this; this.childNodes = [t]; }
  get textContent() { return this.childNodes.map(c => c.textContent).join(''); }
  set href(v) { this.attrs.href = v; }
  set target(v) { this.attrs.target = v; }
  set rel(v) { this.attrs.rel = v; }
  set className(v) { this.attrs.class = v; for (const c of String(v).split(/\\s+/)) if (c) this.classes.add(c); }
  get className() { return this.attrs.class || ''; }
}
class F extends B {
  constructor() { super(Node.FRAGMENT_NODE); }
  get textContent() { return this.childNodes.map(c => c.textContent).join(''); }
}
export const document = {
  createElement: (t) => new E(t),
  createTextNode: (v) => new T(v),
  createDocumentFragment: () => new F(),
};
export function serialize(node) {
  if (node.nodeType === Node.TEXT_NODE) return node.nodeValue;
  const inner = node.childNodes.map(serialize).join('');
  if (node.nodeType === Node.FRAGMENT_NODE) return inner;
  const tag = node.tagName.toLowerCase();          // 직렬화는 outerHTML 처럼 소문자로
  if (tag === 'br') return '<br>';
  const attrs = Object.entries(node.attrs).map(([k, v]) => ` ${k}="${v}"`).join('');
  return `<${tag}${attrs}>${inner}</${tag}>`;
}
"""

_RUNNER = """
import { readFileSync } from 'node:fs';
import { renderMarkdown, serialize } from './renderer.mjs';
const samples = JSON.parse(readFileSync(process.argv[2], 'utf-8'));
process.stdout.write(JSON.stringify(samples.map(s => {
  const frag = renderMarkdown(s);
  return { html: serialize(frag), text: frag.textContent };
})));
"""

_RENDER_WORKDIR = {}


def _render_markdown(samples):
    """마크다운 원문 목록 -> [{'html', 'text'}] (node 가 없으면 skip)."""
    import atexit
    import json
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음 — 실제 렌더 동작 검증 생략")
    work = _RENDER_WORKDIR.get("path")
    if work is None:
        script = _script()
        source = script[script.index("function linkifyUrls("):script.index("// POST 기반 SSE")]
        work = Path(tempfile.mkdtemp(prefix="md_render_"))
        atexit.register(shutil.rmtree, work, True)
        (work / "shim.mjs").write_text(_DOM_SHIM, encoding="utf-8")
        (work / "renderer.mjs").write_text(
            "import { document, Node } from './shim.mjs';\n"
            + source
            + "\nexport { renderMarkdown };\nexport { serialize } from './shim.mjs';\n",
            encoding="utf-8",
        )
        (work / "run.mjs").write_text(_RUNNER, encoding="utf-8")
        _RENDER_WORKDIR["path"] = work
    payload = work / "in.json"
    payload.write_text(json.dumps(samples), encoding="utf-8")
    proc = subprocess.run(
        [node, str(work / "run.mjs"), str(payload)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_renderer_output_matches_supported_syntax():
    """명세 '지원 문법' 표 + 범위 제외(표·인용·HTML)는 원문 유지."""
    cases = [
        ("### API 호출 방법", "<h3>API 호출 방법</h3>"),
        ("# a\n## b\n### c\n#### d", "<h1>a</h1><h2>b</h2><h3>c</h3><h4>d</h4>"),
        ("##### 다섯단계는 미지원", "<p>##### 다섯단계는 미지원</p>"),
        ("**HTTP 메서드:** POST", "<p><strong>HTTP 메서드:</strong> POST</p>"),
        ("- 하나\n- 둘", "<ul><li>하나</li><li>둘</li></ul>"),
        ("1. 하나\n2) 둘", "<ol><li>하나</li><li>둘</li></ol>"),
        ("- 하나\n  - 중첩\n- 둘", "<ul><li>하나<ul><li>중첩</li></ul></li><li>둘</li></ul>"),
        ('```json\n{"a": 1}\n```', '<pre><code>{"a": 1}</code></pre>'),
        ("```\nabc", "<pre><code>abc</code></pre>"),
        ("use `**bold**` here", "<p>use <code>**bold**</code> here</p>"),
        ("a\nb", "<p>a<br>b</p>"),
        ("a\n\nb", "<p>a</p><p>b</p>"),
        ("", ""),
        ("   \n\n  ", ""),
        ("| a | b |\n|---|---|", "<p>| a | b |<br>|---|---|</p>"),
        ("> 인용문", "<p>> 인용문</p>"),
        ("**닫히지 않은", "<p>**닫히지 않은</p>"),
        ("<b>x</b>", "<p><b>x</b></p>"),
        ("## **강조** 제목", "<h2><strong>강조</strong> 제목</h2>"),
    ]
    got = _render_markdown([src for src, _ in cases])
    for (src, expected), result in zip(cases, got):
        assert result["html"] == expected, f"입력 {src!r}"


def test_renderer_makes_safe_links_only():
    """http(s) 만 링크로 만들고, 코드 안 url 은 링크로 만들지 않는다."""
    sources = [
        "[문서](https://a.io/d)",
        "docs: https://a.io/docs 끝",
        "[클릭](javascript:alert(1))",
        "[클릭](data:text/html,x)",
        "`https://a.io`",
        "```\nhttps://a.io\n```",
    ]
    out = _render_markdown(sources)
    assert out[0]["html"] == '<p><a href="https://a.io/d" target="_blank" rel="noopener">문서</a></p>'
    assert out[1]["html"] == (
        '<p>docs: <a href="https://a.io/docs" target="_blank" rel="noopener">https://a.io/docs</a> 끝</p>'
    )
    assert out[2]["html"] == "<p>[클릭](javascript:alert(1))</p>"
    assert out[3]["html"] == "<p>[클릭](data:text/html,x)</p>"
    assert out[4]["html"] == "<p><code>https://a.io</code></p>"
    assert out[5]["html"] == "<pre><code>https://a.io</code></pre>"


def test_rendered_answer_has_no_markdown_symbols_left():
    """티켓 목표: 실제 답변 형태의 원문에서 ###·**·목록 기호가 화면 텍스트에 남지 않는다."""
    answer = (
        "GPTs 등록 API는 다음과 같습니다:\n\n"
        "### POST /v1/gpts\n"
        "- **HTTP 메서드**: POST\n"
        "- **필수 파라미터**:\n"
        "  - `company_seq` (integer)\n\n"
        "출처:\n"
        "- [POST /v1/gpts](https://message-api.qa.hunet.io/docs)\n"
    )
    result = _render_markdown([answer])[0]
    text, html = result["text"], result["html"]
    assert "###" not in text
    assert "**" not in text
    assert not re.search(r"(^|\n)\s*-\s", text)
    assert "](" not in text
    for fragment in ("<h3>", "<strong>", "<ul>", "<li>", "<code>",
                     '<a href="https://message-api.qa.hunet.io/docs"'):
        assert fragment in html, f"{fragment} 가 렌더 결과에 없다"


def test_streaming_partial_input_never_throws():
    """토큰마다 재렌더하므로 잘린 마크다운(열린 **, 열린 ```)에서도 예외가 없어야 한다."""
    answer = '### 제목\n\n**굵게** 와 `코드`\n\n- 목록 https://a.io/x\n\n```json\n{"a":1}\n```\n'
    prefixes = [answer[:i] for i in range(len(answer) + 1)]
    assert len(_render_markdown(prefixes)) == len(prefixes)


def test_rendered_text_nodes_carry_no_newlines():
    """pre 밖 텍스트에 개행이 남으면 pre-wrap 과 겹쳐 줄이 두 번 바뀐다(명세 '이중 줄바꿈 방지')."""
    html = _render_markdown(["a\nb\n\n- c\n- d\n\n### e"])[0]["html"]
    assert "\n" not in html


# --- agent-05 Validator 추가 검증: ask() 스트림 처리 (R3) ---
# 아래 2건(search 앞 본문 비우기 / done·error 없이 끊긴 스트림)은 지금까지 문자열 grep 으로만
# 고정돼 있었다. index.html 의 ask() 원본을 그대로 떼어 위 DOM 셰임 + 가짜 SSE reader 로 돌려
# 화면에 남는 결과를 직접 확인한다(node 가 없으면 skip).

_ASK_RUNNER = """
import { readFileSync } from 'node:fs';
const scenarios = JSON.parse(readFileSync(process.argv[2], 'utf-8'));
const enc = new TextEncoder();
const okFetch = async () => ({ ok: true, status: 200, body: { getReader: () => globalThis.__reader } });
globalThis.fetch = okFetch;
const { ask, log, Node, serialize } = await import('./ask.mjs');

function makeReader(text, size) {   // 프레임 경계와 무관하게 쪼개 버퍼링도 함께 태운다
  const bytes = enc.encode(text);
  let i = 0;
  return { read: async () => {
    if (i >= bytes.length) return { value: undefined, done: true };
    const v = bytes.slice(i, i + size); i += size;
    return { value: v, done: false };
  } };
}
const out = [];
for (const events of scenarios) {
  if (events === 'fetch-fail') {   // 서버 다운: fetch 자체가 실패하는 경우
    const before = log.childNodes.length;
    globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
    let thrown = null;
    try { await ask('질문'); } catch (e) { thrown = e.message; }
    globalThis.fetch = okFetch;
    out.push({ thrown, added: log.childNodes.length - before });
    continue;
  }
  const sse = events.map(([e, d]) => `event: ${e}\\ndata: ${JSON.stringify(d)}\\n\\n`).join('');
  globalThis.__reader = makeReader(sse, 7);
  await ask('질문');
  const wrap = log.childNodes[log.childNodes.length - 1];
  const kids = wrap.childNodes.filter(c => c.nodeType === Node.ELEMENT_NODE);
  const body = kids.find(c => c.className === 'body');
  out.push({
    text: body.textContent,
    html: serialize(body),
    err: body.classes.has('err'),
    search: kids.filter(c => c.className === 'search').map(c => c.textContent),
  });
}
process.stdout.write(JSON.stringify(out));
"""

_ASK_WORKDIR = {}


def _run_ask(scenarios):
    """[[ [event, data], ... ], ...] -> [{'text', 'html', 'err', 'search'}] (node 가 없으면 skip)."""
    import atexit
    import json
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음 — ask() 실행 검증 생략")
    work = _ASK_WORKDIR.get("path")
    if work is None:
        script = _script()
        source = script[script.index("function addMessage("):script.index("form.addEventListener")]
        assert "async function ask(" in source
        work = Path(tempfile.mkdtemp(prefix="ask_run_"))
        atexit.register(shutil.rmtree, work, True)
        (work / "shim.mjs").write_text(_DOM_SHIM, encoding="utf-8")
        (work / "ask.mjs").write_text(
            "import { document, Node } from './shim.mjs';\n"
            "const threadId = 'thread-test';\n"
            "const log = document.createElement('div');\n"
            + source
            + "\nexport { ask, log };\nexport { serialize, Node } from './shim.mjs';\n",
            encoding="utf-8",
        )
        (work / "run.mjs").write_text(_ASK_RUNNER, encoding="utf-8")
        _ASK_WORKDIR["path"] = work
    payload = work / "in.json"
    payload.write_text(json.dumps(scenarios), encoding="utf-8")
    proc = subprocess.run(
        [node, str(work / "run.mjs"), str(payload)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


_SEARCH_DATA = {"tool": "search_openapi", "query": "메시지 등록"}


def test_ask_drops_preamble_and_shows_only_the_final_answer():
    """멘트 token → search → 답변 token → done: 본문은 답변만, [검색] 줄 1개, 오류 표시 없음."""
    result = _run_ask([[
        ["token", "문서를 먼저 "], ["token", "검색해 볼게요."],
        ["search", _SEARCH_DATA],
        ["token", "POST /v1/messages "], ["token", "입니다."],
        ["done", ""],
    ]])[0]

    assert result["text"] == "POST /v1/messages 입니다."
    assert "검색해 볼게요" not in result["text"]
    assert result["search"] == ["[검색] search_openapi(메시지 등록)"]
    assert result["err"] is False


def test_ask_marks_stream_interrupted_when_done_is_missing():
    """done/error 없이 끊기면 부분 답변 뒤에 중단 안내가 붙고 오류 표시가 켜진다."""
    result = _run_ask([[
        ["search", _SEARCH_DATA],
        ["token", "POST /v1/messages "], ["token", "입니"],
    ]])[0]

    assert result["text"] == "POST /v1/messages 입니\n응답이 중단되었습니다."
    assert result["err"] is True


def test_ask_error_frame_shows_detail_without_interruption_notice():
    """error 프레임은 정상 종결로 보고 중단 안내를 덧붙이지 않는다."""
    result = _run_ask([[
        ["token", "부분 답변"],
        ["error", "ResponseError: model \"qwen3:14b\" not found"],
    ]])[0]

    assert result["err"] is True
    assert result["text"].endswith('ResponseError: model "qwen3:14b" not found')
    assert "응답이 중단되었습니다" not in result["text"]


def test_ask_without_search_keeps_the_whole_answer():
    """도구 호출이 없는 턴은 첫 token 부터 전부 답변이다(비우기 로직이 정상 답변을 먹지 않는다)."""
    result = _run_ask([[
        ["token", "안녕하세요. "], ["token", "무엇을 도와드릴까요?"],
        ["done", ""],
    ]])[0]

    assert result["text"] == "안녕하세요. 무엇을 도와드릴까요?"
    assert result["search"] == []
    assert result["err"] is False


def test_ask_renders_markdown_answer_after_search():
    """search 로 비운 뒤에도 이어지는 token 은 마크다운으로 렌더된다(agent-03 계약 유지)."""
    result = _run_ask([[
        ["token", "검색할게요."],
        ["search", _SEARCH_DATA],
        ["token", "### 제목\n\n- **굵게** 항목\n"],
        ["done", ""],
    ]])[0]

    assert "<h3>제목</h3>" in result["html"]
    assert "<strong>굵게</strong>" in result["html"]
    assert "###" not in result["text"] and "**" not in result["text"]


def test_ask_fetch_failure_leaves_no_empty_answer_bubble():
    """fetch 자체가 실패하면(서버 다운) 빈 답변 말풍선을 만들지 않고 예외를 호출자(요청 실패 표시)에 넘긴다."""
    result = _run_ask(["fetch-fail"])[0]

    assert result["thrown"] == "Failed to fetch"
    assert result["added"] == 0


# --- agent-09: 대화 이어하기(localStorage + 히스토리 복원) ---

def test_thread_id_is_persisted_in_local_storage():
    script = _script()
    assert "localStorage.getItem('kudos.threadId')" in script
    assert script.count("localStorage.setItem('kudos.threadId', threadId)") == 2  # 최초 + 새 대화


def test_new_conversation_stores_new_thread_id():
    listener = _script().split("resetBtn.addEventListener", 1)[1]
    assert "threadId = crypto.randomUUID()" in listener
    assert "localStorage.setItem('kudos.threadId', threadId)" in listener
    assert re.search(r"log\.(textContent\s*=\s*''|replaceChildren\(\))", listener)


def test_history_is_restored_on_load():
    script = _script()
    body = _function_body("restoreHistory")
    assert "/v1/threads/" in body and "encodeURIComponent(threadId)" in body
    assert "/messages" in body
    assert "restoreHistory();" in script                      # 로드 시 1회 호출


def test_restored_history_is_inserted_without_html_strings():
    """복원한 대화도 textContent/renderMarkdown 으로만 그린다."""
    body = _function_body("restoreHistory")
    assert "addMessage('q'" in body                            # 질문 말풍선(textContent)
    assert "renderMarkdown(m.content)" in body                 # 답변은 마크다운 렌더
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in body


def test_history_failure_is_silent():
    """서버가 없거나 응답이 이상하면 조용히 빈 화면(새 대화와 같음)으로 둔다."""
    body = _function_body("restoreHistory")
    assert "catch" in body
    assert "return" in body
    assert "요청 실패" not in body


# --- agent-15: 채팅 UI 리디자인 (겉모습만, 서버 계약 불변) ---

# U2. 입력창(textarea + Enter 전송)

def test_composer_uses_textarea_with_same_ids():
    html = _html()
    assert re.search(r'<textarea[^>]+id="question"', html)
    assert not re.search(r'<input[^>]+id="question"', html)
    for element_id in ("form", "send", "reset", "log", "question"):
        assert f'id="{element_id}"' in html


def test_enter_sends_and_shift_enter_makes_a_newline():
    script = _script()
    handler = script[script.index("input.addEventListener('keydown'"):]
    assert "e.key !== 'Enter' || e.shiftKey" in handler      # Shift+Enter 는 그냥 줄바꿈
    assert "e.isComposing" in handler                        # 한글 조합 중에는 보내지 않는다
    assert "e.preventDefault()" in handler
    assert "requestSubmit" in handler                        # 폼 submit 경로를 그대로 탄다


def test_empty_input_is_still_ignored():
    submit = _script()[_script().index("form.addEventListener('submit'"):]
    assert "input.value.trim()" in submit
    assert re.search(r"if\s*\(\s*!q\s*\)\s*return", submit)


def test_textarea_grows_up_to_a_limit():
    script = _script()
    assert "function autoGrow()" in script
    assert "input.scrollHeight" in script
    assert re.search(r"#question\s*\{[^}]*max-height", _style())    # 그 이상은 스크롤


# U3. 스트리밍 커서

def test_streaming_cursor_is_a_css_pseudo_element():
    """커서를 DOM 요소로 만들지 않아 답변 textContent 가 오염되지 않는다."""
    assert re.search(r"\.body\.streaming::after\s*\{[^}]*content", _style())
    assert "@keyframes blink" in _style()


def test_cursor_turns_on_while_streaming_and_off_when_finished():
    script = _script()
    token = script[script.index("if (event === 'token')"):script.index("else if (event === 'search')")]
    assert "classList.add('streaming')" in token

    finish = _function_body("finishAnswer")
    assert "classList.remove('streaming')" in finish
    # done·error·중단 세 경로 모두에서 커서가 꺼진다
    assert re.search(r"else if \(event === 'done'\)[^\n]*finishAnswer", script)
    assert re.search(r"else if \(event === 'error'\)[^\n]*finishAnswer", script)
    assert re.search(r"body\.classList\.remove\('streaming'\);\s*\n\s*if \(!ended\)", script)


# U4·U5. 코드블록 후처리와 복사

def test_code_block_decoration_is_post_processing_not_renderer():
    """renderMarkdown 은 그대로 두고 decorateCodeBlocks 가 <pre> 를 감싼다."""
    renderer = _script()[_script().index("function renderMarkdown("):_script().index("// 서버에 남아 있는 이전 대화")]
    assert "codeblock" not in renderer and "codebar" not in renderer

    decorate = _function_body("decorateCodeBlocks")
    assert "createElement('div')" in decorate
    assert "'codeblock'" in decorate and "'codebar'" in decorate
    assert "replaceChild" in decorate
    assert "innerHTML" not in decorate


def test_code_block_language_comes_from_the_fence():
    body = _function_body("fenceLanguages")
    assert "```" in body
    assert "gm" in body                     # 여러 줄에서 여는 펜스만 골라낸다


def test_copy_button_is_skipped_without_clipboard_api():
    body = _function_body("copyButton")
    assert "navigator.clipboard" in body
    assert re.search(r"return null", body)
    assert "writeText" in body


def test_answer_copy_uses_the_raw_markdown():
    finish = _function_body("finishAnswer")
    assert "copyButton(() => raw, '복사')" in finish
    assert "answer-copy" in finish
    decorate = _function_body("decorateCodeBlocks")
    assert "copyButton(() => pre.textContent, '복사')" in decorate   # 코드 복사는 그 블록만


# U6. 다크 모드

def test_dark_mode_only_redefines_colour_variables():
    style = _style()
    blocks = re.findall(r"@media \(prefers-color-scheme: dark\)\s*\{(.*?)\n  \}", style, re.S)
    assert blocks, "다크 모드 블록이 없다"
    joined = "\n".join(blocks)
    assert "--bg:" in joined and "--text:" in joined
    assert "display:" not in joined and "position:" not in joined   # 구조 CSS 는 재정의하지 않는다


# U7. 스크롤

def test_scroll_sticks_to_bottom_only_when_user_is_at_the_bottom():
    stick = _function_body("stickToBottom")
    assert "nearBottom()" in stick
    assert "showToBottom(true)" in stick
    assert "scrollIntoView" in stick

    near = _function_body("nearBottom")
    assert "window.scrollY" in near
    assert "typeof window === 'undefined'" in near        # DOM 셰임에서도 안전하다


def test_scroll_to_bottom_button_exists_and_toggles():
    assert 'id="tobottom"' in _html()
    assert re.search(r"#tobottom\.show\s*\{", _style())
    assert re.search(r"#tobottom\s*\{[^}]*display\s*:\s*none", _style())
    script = _script()
    assert "showToBottom(!nearBottom())" in script


# D1·D2. 레이아웃·아바타

def test_layout_is_768_and_answers_have_no_card():
    style = _style()
    assert re.search(r"main\s*\{[^}]*max-width:\s*768px", style)
    assert re.search(r"\.msg\s*\{(?:(?!\})[\s\S])*\}", style)
    msg_rule = re.search(r"\.msg\s*\{([^}]*)\}", style).group(1)
    assert "border:" not in msg_rule                      # 답변 카드 테두리 제거
    assert re.search(r"\.msg\.q\s*\{[^}]*background:\s*var\(--bubble\)", style)


def test_avatars_are_css_pseudo_elements_without_images():
    style = _style()
    assert re.search(r"\.msg::before\s*\{[^}]*content:\s*\"A\"", style)
    assert re.search(r"\.msg\.q::before\s*\{[^}]*content:\s*\"나\"", style)
    assert "url(" not in style                            # 이미지·아이콘 폰트 없음


def test_tool_call_line_is_a_pill():
    assert re.search(r"\.search\s*\{[^}]*border-radius:\s*999px", _style())


# U4 실행 검증: 렌더 결과에 후처리를 적용한 DOM 을 node 로 직접 확인한다.

_DECORATE_RUNNER = """
import { readFileSync } from 'node:fs';
const samples = JSON.parse(readFileSync(process.argv[2], 'utf-8'));
const { renderMarkdown, decorateCodeBlocks, document, serialize } = await import('./decorate.mjs');
process.stdout.write(JSON.stringify(samples.map(raw => {
  const plain = document.createElement('div');
  plain.replaceChildren(renderMarkdown(raw));
  const decorated = document.createElement('div');
  decorated.replaceChildren(renderMarkdown(raw));
  decorateCodeBlocks(decorated, raw);
  return { plain: serialize(plain), decorated: serialize(decorated) };
})));
"""

_DECORATE_WORKDIR = {}


def _decorate(samples):
    """마크다운 원문 -> [{'plain', 'decorated'}] (node 가 없으면 skip)."""
    import atexit
    import json
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음 — 후처리 실행 검증 생략")
    work = _DECORATE_WORKDIR.get("path")
    if work is None:
        script = _script()
        source = script[script.index("function linkifyUrls("):script.index("form.addEventListener")]
        work = Path(tempfile.mkdtemp(prefix="decorate_"))
        atexit.register(shutil.rmtree, work, True)
        (work / "shim.mjs").write_text(_DOM_SHIM, encoding="utf-8")
        (work / "decorate.mjs").write_text(
            "import { document, Node } from './shim.mjs';\n"
            "const threadId = 'thread-test';\n"
            + source
            + "\nexport { renderMarkdown, decorateCodeBlocks, document };\n"
              "export { serialize } from './shim.mjs';\n",
            encoding="utf-8",
        )
        (work / "run.mjs").write_text(_DECORATE_RUNNER, encoding="utf-8")
        _DECORATE_WORKDIR["path"] = work
    payload = work / "in.json"
    payload.write_text(json.dumps(samples), encoding="utf-8")
    proc = subprocess.run([node, str(work / "run.mjs"), str(payload)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_decoration_adds_a_bar_only_after_post_processing():
    result = _decorate(["```python\nprint(1)\n```"])[0]

    assert result["plain"] == "<div><pre><code>print(1)</code></pre></div>"     # 렌더러 출력 불변
    assert "codeblock" in result["decorated"]
    assert "<pre><code>print(1)</code></pre>" in result["decorated"]            # 코드 내용 그대로
    assert ">python<" in result["decorated"]                                    # 언어 라벨
    assert "button" not in result["decorated"]                                  # 클립보드 없으면 복사 버튼 없음


def test_decoration_labels_each_block_and_defaults_to_code():
    result = _decorate(["```js\na\n```\n\n텍스트\n\n```\nb\n```"])[0]

    assert result["decorated"].count("codeblock") == 2
    assert ">js<" in result["decorated"]
    assert ">code<" in result["decorated"]          # 언어 표기가 없으면 기본 라벨


def test_decoration_leaves_non_code_answers_untouched():
    result = _decorate(["### 제목\n\n- **굵게** 항목"])[0]
    assert result["plain"] == result["decorated"]


# U12. 빈 화면 예시 질문 (D10)

def test_examples_container_lives_outside_the_log():
    html = _html()
    assert '<div id="examples"' in html
    log_block = html[html.index('<div id="log"'):html.index('<form id="form"')]
    assert log_block.index('id="log"') < log_block.index('id="examples"')
    assert '<div id="log"></div>' in html          # 로그는 여전히 비어 있는 컨테이너


def test_examples_are_a_named_constant_array():
    script = _script()
    assert re.search(r"const EXAMPLES = \[", script)
    items = re.search(r"const EXAMPLES = \[(.*?)\];", script, re.S).group(1)
    assert len(re.findall(r'"[^"]+"', items)) >= 3
    assert "자유롭게 고치면 된다" in script          # 사용자가 직접 고칠 수 있다는 안내


def test_example_click_reuses_the_existing_submit_path():
    body = _function_body("buildExamples")
    assert "input.value = text" in body
    assert "form.requestSubmit()" in body
    assert "ask(" not in body                      # 전송 로직을 복제하지 않는다
    assert "createElement('button')" in body and "'button'" in body
    assert "innerHTML" not in body


def test_examples_show_only_when_the_log_is_empty():
    body = _function_body("showExamples")
    assert "log.childNodes.length === 0" in body
    assert "'show'" in body
    script = _script()
    # 첫 전송·새 대화·복원 완료 세 시점에서 다시 판정한다
    submit = script[script.index("form.addEventListener('submit'"):script.index("resetBtn.addEventListener")]
    assert "showExamples()" in submit
    reset = script[script.index("resetBtn.addEventListener"):script.index("function buildExamples(")]
    assert "showExamples()" in reset
    assert "restored.finally(showExamples)" in script


def test_examples_are_locked_while_answering():
    script = _script()
    submit = script[script.index("form.addEventListener('submit'"):script.index("resetBtn.addEventListener")]
    before, after = submit.split("try {", 1)
    assert "setExamplesDisabled(true)" in before
    assert "setExamplesDisabled(false)" in after.split("finally", 1)[1]
    assert "button.disabled = on" in _function_body("setExamplesDisabled")


def test_examples_have_their_own_styles_outside_msg():
    style = _style()
    assert re.search(r"#examples\s*\{[^}]*display\s*:\s*none", style)
    assert re.search(r"#examples\.show\s*\{[^}]*display\s*:\s*grid", style)   # D11: 2×2 카드


# U13. 빈 화면 히어로 (D11)

def test_empty_screen_shows_hero_and_examples():
    html = _html()
    assert '<div id="hero">' in html
    assert re.search(r"<h2>[^<]+</h2>", html)                      # 큰 제목 한 줄
    style = _style()
    assert re.search(r"body\.empty #hero\s*\{[^}]*display\s*:\s*block", style)
    assert re.search(r"#hero\s*\{[^}]*display\s*:\s*none", style)  # 대화가 있으면 숨김


def test_empty_class_follows_the_same_log_check():
    body = _function_body("showExamples")
    assert "log.childNodes.length === 0" in body
    assert "document.body.classList" in body and "'empty'" in body
    assert body.count("empty ? 'add' : 'remove'") == 2             # 예시와 레이아웃이 같은 조건


def test_layout_switch_is_css_only_and_form_stays_in_place():
    """form·textarea·버튼을 DOM 에서 옮기지 않는다(클래스 + CSS 배치로만 전환)."""
    html = _html()
    main = html[html.index("<main>"):html.index("</main>")]
    order = [m for m in re.findall(r'id="(hero|log|examples|form|question|send|tobottom)"', main)]
    assert order == ["hero", "log", "examples", "form", "question", "send", "tobottom"]
    # form 의 부모는 여전히 main, textarea·send 의 부모는 여전히 composer
    assert re.search(r'<form id="form">\s*<div class="composer">\s*<textarea id="question"', main)
    assert re.search(r'<textarea id="question"[\s\S]*?<button id="send"', main)

    script = _script()
    for moved in ("appendChild(form", "insertBefore(form", "append(form", "form.remove("):
        assert moved not in script

    style = _style()
    assert re.search(r"body\.empty main\s*\{[^}]*justify-content\s*:\s*flex-start", style)   # D16
    assert re.search(r"body\.empty form\s*\{[^}]*position\s*:\s*static", style)


def test_examples_are_four_labelled_cards():
    script = _script()
    items = re.search(r"const EXAMPLES = \[(.*?)\n\];", script, re.S).group(1)
    assert len(re.findall(r"\{\s*label:", items)) == 4
    assert len(re.findall(r"text:\s*\"", items)) == 4

    body = _function_body("buildExamples")
    assert "const { label, text } of EXAMPLES" in body
    assert "tag.textContent = label" in body
    assert "input.value = text" in body                            # 전송되는 값은 text
    assert "form.requestSubmit()" in body


def test_example_grid_is_two_columns_with_narrow_fallback():
    style = _style()
    assert re.search(r"#examples\.show\s*\{[^}]*repeat\(2,", style)
    assert re.search(r"@media \(max-width: \d+px\)\s*\{\s*#examples\.show\s*\{[^}]*1fr", style)


# U14. 헤더 상태 (D12)

def test_health_element_keeps_id_and_live_region():
    assert re.search(r'<span id="health" aria-live="polite"', _html())


def test_health_shows_short_label_with_full_text_in_attributes():
    body = _function_body("setHealth")
    assert "el.textContent = short" in body
    assert "el.title = full" in body
    assert "setAttribute('aria-label', full)" in body

    refresh = _function_body("refreshHealth")
    assert "RAG ${h.rag ? '연결됨' : '끊김'}" in refresh             # 전체 문구는 그대로 전달
    assert "Ollama ${h.ollama ? '연결됨' : '끊김'}" in refresh
    assert "상태 확인 실패" in refresh


def test_health_has_three_coloured_states():
    style = _style()
    for state in ("ok", "degraded", "down"):
        assert re.search(rf"#health\.{state}::before\s*\{{[^}}]*background", style)
    refresh = _function_body("refreshHealth")
    assert "'ok' : 'degraded'" in refresh
    assert "setHealth('down'" in refresh


# U15. 글자 크기 (D13)

def _px(pattern):
    """`선택자 { ... font-size: N px ... }` 에서 숫자만 뽑는다."""
    m = re.search(pattern + r"\s*\{[^}]*font-size\s*:\s*([\d.]+)px", _style())
    assert m, f"{pattern} 의 font-size 를 찾지 못했다"
    return float(m.group(1))


def test_base_font_size_is_a_single_variable():
    style = _style()
    assert re.search(r":root\s*\{[^}]*--fs:\s*17px", style)
    assert "이 값만 바꾸면 전체가 따라 움직인다" in style     # 사용자가 한 줄로 조정할 수 있다


def test_body_and_messages_use_the_base_size():
    style = _style()
    assert re.search(r"body\s*\{[^}]*font-size\s*:\s*var\(--fs\)", style)
    assert re.search(r"body\s*\{[^}]*line-height\s*:\s*1\.7", style)
    assert re.search(r"\.msg\s*\{[^}]*font-size\s*:\s*var\(--fs\)", style)
    assert re.search(r"\.msg\s*\{[^}]*line-height\s*:\s*1\.7", style)


def test_markdown_headings_form_a_visible_hierarchy():
    h1, h2, h3, h4 = (_px(rf"\.msg h{n}") for n in (1, 2, 3, 4))
    assert h1 > h2 > h3 > h4
    assert (h1, h2, h3, h4) == (22, 19, 17.5, 16)
    assert h3 > 17            # 본문(17px)보다 큰 단계가 h3 까지


def test_code_and_pill_sizes_are_readable():
    assert _px(r"\.msg pre") == 14
    assert _px(r"\.msg code") == 14
    assert _px(r"\.search") == 13
    assert _px(r"\.codebar") == 13


def test_header_and_composer_follow_the_base_size():
    style = _style()
    assert re.search(r"header h1\s*\{[^}]*font-size\s*:\s*var\(--fs\)", style)
    assert re.search(r"#question\s*\{[^}]*font-size\s*:\s*var\(--fs\)", style)   # 입력·출력 같은 크기


def test_example_cards_are_larger_than_before():
    style = _style()
    assert _px(r"#examples \.tag") == 13
    assert re.search(r"#examples \.text\s*\{[^}]*font-size\s*:\s*calc\(var\(--fs\)", style)


def test_dark_mode_does_not_redefine_any_size():
    """라이트/다크는 색만 다르다. 크기를 중복 정의하면 --fs 한 줄 조정이 깨진다."""
    blocks = re.findall(r"@media \(prefers-color-scheme: dark\)\s*\{(.*?)\n  \}", _style(), re.S)
    joined = "\n".join(blocks)
    for size_property in ("font-size", "line-height", "--fs", "width:", "padding"):
        assert size_property not in joined


def test_examples_cover_confluence_not_only_openapi():
    """코퍼스는 Confluence 가 대부분이다(D14). 예시가 OpenAPI 쪽으로만 쏠리지 않게 고정한다."""
    items = re.findall(r'\{\s*label:\s*"([^"]+)",\s*text:\s*"([^"]+)"\s*\}',
                       re.search(r"const EXAMPLES = \[(.*?)\n\];", _script(), re.S).group(1))
    assert len(items) == 4
    assert len({label for label, _ in items}) == 4                 # 라벨 중복 없음

    non_api = [text for _, text in items if "api" not in text.lower()]
    assert len(non_api) >= 2, f"Confluence 유형 예시가 부족하다: {items}"
    assert any("openapi.json" in text for _, text in items)        # 실호출 유형도 남긴다


# D16. 빈 화면 여백 (입력창과 붙어 보이지 않게)

def test_empty_screen_group_sits_near_the_top():
    style = _style()
    main_rule = re.search(r"body\.empty main\s*\{([^}]*)\}", style).group(1)
    assert "justify-content:flex-start" in main_rule.replace(" ", "")
    assert "center" not in main_rule
    assert re.search(r"padding-top:\s*clamp\(\s*\d+px,\s*\d+vh,\s*\d+px\s*\)", main_rule)


def test_examples_keep_distance_from_the_composer():
    style = _style()
    rule = re.search(r"body\.empty #examples\s*\{([^}]*)\}", style).group(1)
    assert re.search(r"margin-bottom:\s*clamp\(\s*\d+px,\s*\d+vh,\s*\d+px\s*\)", rule)
    # 히어로↔예시 간격은 그대로 둔다(한 묶음으로 읽혀야 한다)
    assert not re.search(r"body\.empty #hero\s*\{[^}]*margin", style)


def test_empty_screen_spacing_shrinks_on_short_windows():
    """clamp 의 하한·vh 값이 작은 창에서도 화면을 넘기지 않을 정도여야 한다."""
    style = _style()
    top = re.search(r"body\.empty main\s*\{[^}]*padding-top:\s*clamp\((\d+)px,\s*(\d+)vh,\s*(\d+)px\)", style)
    gap = re.search(r"body\.empty #examples\s*\{[^}]*margin-bottom:\s*clamp\((\d+)px,\s*(\d+)vh,\s*(\d+)px\)", style)
    assert top and gap
    # 600px 높이 창에서 두 여백의 합
    at_600 = min(max(int(top.group(1)), 600 * int(top.group(2)) / 100), int(top.group(3))) \
        + min(max(int(gap.group(1)), 600 * int(gap.group(2)) / 100), int(gap.group(3)))
    assert at_600 <= 200, f"작은 창에서 여백이 과하다: {at_600}px"


def test_form_position_switches_without_moving_in_dom():
    style = _style()
    assert re.search(r"body\.empty form\s*\{[^}]*position\s*:\s*static", style)
    assert re.search(r"\n  form\s*\{[^}]*position\s*:\s*sticky", style)   # 대화 중에는 하단 고정
    script = _script()
    for moved in ("appendChild(form", "insertBefore(form", "append(form", "form.remove("):
        assert moved not in script


# D16 보정. 두 상태 모두 입력창이 뷰포트 바닥 모서리에 닿지 않는다

def test_composer_floats_above_the_viewport_bottom_while_chatting():
    rule = re.search(r"\n  form\s*\{([^}]*)\}", _style()).group(1)
    assert "position:sticky" in rule.replace(" ", "")
    bottom = re.search(r"bottom:\s*clamp\((\d+)px,\s*(\d+)vh,\s*(\d+)px\)", rule)
    assert bottom, "sticky 입력창이 바닥에 붙어 있다"
    assert int(bottom.group(1)) > 0                       # 최소값도 0 이 아니다


def test_last_answer_is_not_hidden_behind_the_composer():
    main_rule = re.search(r"\n  main\s*\{([^}]*)\}", _style()).group(1)
    assert re.search(r"padding:[^;]*clamp\(\s*\d+px,\s*\d+vh,\s*\d+px\s*\)", main_rule)


def test_empty_screen_composer_is_part_of_the_top_group():
    style = _style()
    rule = re.search(r"body\.empty form\s*\{([^}]*)\}", style).group(1)
    assert "position:static" in rule.replace(" ", "")     # 바닥 고정이 아니다
    assert "margin-top:0" in rule.replace(" ", "")        # 예시 바로 아래(간격은 #examples 가 담당)


# --- agent-15 Validator 추가 검증 ---
# 위 agent-15 계약 테스트 상당수가 스크립트 문자열 grep 이라, 코드가 있어도 "동작"은 고정되지 않는다.
# 아래는 index.html 의 원본 코드를 그대로 떼어 최소 DOM 셰임 위에서 node 로 실제 실행한다.
# (셰임에 없는 addEventListener/append 는 별도 파일에서 프로토타입에 덧댄다 — _DOM_SHIM 은 손대지 않는다)

_SHIM_PATCH = """
import { document } from './shim.mjs';
const proto = Object.getPrototypeOf(document.createElement('div'));
proto.addEventListener = function (type, fn) { (this._h ||= {}); (this._h[type] ||= []).push(fn); };
proto.dispatch = function (type, ev) { for (const fn of ((this._h || {})[type] || [])) fn(ev); };
proto.append = function (...nodes) { for (const n of nodes) this.appendChild(n); };
// 실제 브라우저의 Element.tagName 은 HTML 문서에서 항상 대문자다(DOM 표준).
// _DOM_SHIM 은 소문자를 넣어 두었는데, tagName 을 비교하는 코드는 그 차이에서 갈리므로
// 여기서는 브라우저와 같은 대문자로 맞춘다(헤드리스 Chrome 실측: <pre> -> "PRE", <button> -> "BUTTON").
const create = document.createElement;
document.createElement = (tag) => {
  const el = create(tag);
  Object.defineProperty(el, 'tagName', { value: String(tag).toUpperCase(), configurable: true });
  return el;
};
document.body = document.createElement('body');
"""


def _node_run(prefix, files, runner, payload_obj):
    """임시 디렉터리에 셰임 + 대상 모듈을 쓰고 node 로 실행한다(node 가 없으면 skip)."""
    import json
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음 — 실행 검증 생략")
    work = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        (work / "shim.mjs").write_text(_DOM_SHIM, encoding="utf-8")
        (work / "patch.mjs").write_text(_SHIM_PATCH, encoding="utf-8")
        for name, source in files.items():
            (work / name).write_text(source, encoding="utf-8")
        (work / "run.mjs").write_text(runner, encoding="utf-8")
        payload = work / "in.json"
        payload.write_text(json.dumps(payload_obj), encoding="utf-8")
        proc = subprocess.run([node, str(work / "run.mjs"), str(payload)],
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _ui_source() -> str:
    """addMessage ~ ask 까지(렌더러·후처리·복원 포함) 원본 그대로."""
    script = _script()
    source = script[script.index("function addMessage("):script.index("form.addEventListener")]
    assert "async function ask(" in source and "function finishAnswer(" in source
    return source


# U2. 입력 동작 — 실제 키 이벤트를 흘려 본다

_KEY_RUNNER = """
import { readFileSync } from 'node:fs';
const { handlers, form, input, state } = await import('./keys.mjs');
const events = JSON.parse(readFileSync(process.argv[2], 'utf-8'));
const out = [];
for (const ev of events) {
  state.submits = 0; state.prevented = 0;
  handlers.keydown({ ...ev, preventDefault: () => { state.prevented++; } });
  out.push({ submits: state.submits, prevented: state.prevented });
}
// 입력 이벤트로 높이가 다시 계산되는지(D3 자동 확장)
input.style.height = '1px';
input.scrollHeight = 123;
handlers.input({});
out.push({ height: input.style.height });
process.stdout.write(JSON.stringify(out));
"""


def _run_keys(events):
    script = _script()
    source = script[script.index("// 내용에 맞춰 높이를 늘리되"):script.index("const toBottom =")]
    assert "addEventListener('keydown'" in source
    module = (
        "export const state = { submits: 0, prevented: 0 };\n"
        "export const handlers = {};\n"
        "export const input = { style: {}, scrollHeight: 40, value: '',\n"
        "  addEventListener: (t, fn) => { handlers[t] = fn; } };\n"
        "export const form = { requestSubmit: () => { state.submits++; } };\n"
        + source
    )
    return _node_run("ui_keys_", {"keys.mjs": module}, _KEY_RUNNER, events)


def test_enter_actually_submits_and_shift_enter_does_not():
    plain, shift, composing, other = _run_keys([
        {"key": "Enter", "shiftKey": False, "isComposing": False},
        {"key": "Enter", "shiftKey": True, "isComposing": False},
        {"key": "Enter", "shiftKey": False, "isComposing": True},
        {"key": "a", "shiftKey": False, "isComposing": False},
    ])[:4]

    assert plain == {"submits": 1, "prevented": 1}       # Enter → 전송(줄바꿈 막음)
    assert shift == {"submits": 0, "prevented": 0}       # Shift+Enter → 줄바꿈만
    assert composing == {"submits": 0, "prevented": 0}   # 한글 조합 중에는 전송하지 않는다
    assert other == {"submits": 0, "prevented": 0}


def test_input_event_recomputes_the_textarea_height():
    assert _run_keys([])[-1] == {"height": "123px"}


# U3. 스트리밍 커서가 실제로 켜졌다가 꺼진다

_CURSOR_RUNNER = """
import { readFileSync } from 'node:fs';
const enc = new TextEncoder();
globalThis.fetch = async () => ({ ok: true, status: 200, body: { getReader: () => globalThis.__reader } });
const { ask, log } = await import('./ui.mjs');

function lastBody() {
  const wrap = log.childNodes[log.childNodes.length - 1];
  return wrap.childNodes.find(c => c.className === 'body');
}
const scenarios = JSON.parse(readFileSync(process.argv[2], 'utf-8'));
const out = [];
for (const { head, tail } of scenarios) {
  const frames = (evs) => evs.map(([e, d]) => `event: ${e}\\ndata: ${JSON.stringify(d)}\\n\\n`).join('');
  const chunks = [enc.encode(frames(head)), enc.encode(frames(tail))];
  let i = 0, mid = null;
  globalThis.__reader = { read: async () => {
    if (i === 1) mid = lastBody().classes.has('streaming');   // 마지막 프레임 직전 상태
    if (i >= chunks.length) return { value: undefined, done: true };
    return { value: chunks[i++], done: false };
  } };
  await ask('질문');
  out.push({ mid, final: lastBody().classes.has('streaming') });
}
process.stdout.write(JSON.stringify(out));
"""


def _run_cursor(scenarios):
    module = ("import './patch.mjs';\n"
              "import { document, Node } from './shim.mjs';\n"
              "const threadId = 'thread-test';\n"
              "const log = document.createElement('div');\n"
              + _ui_source()
              + "\nexport { ask, log };\n")
    return _node_run("ui_cursor_", {"ui.mjs": module}, _CURSOR_RUNNER, scenarios)


def test_streaming_cursor_is_on_during_tokens_and_off_at_the_end():
    """token 중에는 커서 클래스가 붙고, done·error·중단 세 경로 모두에서 떨어진다."""
    done, error, cut = _run_cursor([
        {"head": [["token", "부분 "]], "tail": [["token", "답변"], ["done", ""]]},
        {"head": [["token", "부분 "]], "tail": [["error", "boom"]]},
        {"head": [["token", "부분 "]], "tail": [["token", "답변"]]},
    ])

    for name, result in (("done", done), ("error", error), ("중단", cut)):
        assert result["mid"] is True, f"{name}: 스트리밍 중 커서가 없다"
        assert result["final"] is False, f"{name}: 종료 후 커서가 남았다"


# U5. 복사 버튼이 실제로 무엇을 복사하는가

_COPY_RUNNER = """
import { readFileSync } from 'node:fs';
const copied = [];
Object.defineProperty(globalThis, 'navigator', {
  value: { clipboard: { writeText: (t) => copied.push(t) } }, configurable: true,
});
globalThis.setTimeout = () => 0;              // 라벨 되돌리기 타이머는 검증 대상이 아니다
const { addAnswer, finishAnswer, renderMarkdown, log, Node } = await import('./ui.mjs');
const raw = JSON.parse(readFileSync(process.argv[2], 'utf-8'));

const { wrap, body } = addAnswer();
body.replaceChildren(renderMarkdown(raw));
finishAnswer(wrap, body, raw);

const kids = wrap.childNodes.filter(c => c.nodeType === Node.ELEMENT_NODE);
const answerCopy = kids.find(c => c.className === 'copy answer-copy');
const blocks = body.childNodes.filter(c => c.className === 'codeblock');
const labels = blocks.map(b => b.childNodes[0].childNodes[0].textContent);
const codeCopies = blocks.map(b => b.childNodes[0].childNodes.find(c => c.className === 'copy'))
                         .filter(Boolean);

const answerLabel = answerCopy ? answerCopy.textContent : null;   // 클릭 전 라벨
if (answerCopy) answerCopy.dispatch('click', {});
const afterAnswer = copied.slice();
for (const c of codeCopies) c.dispatch('click', {});
process.stdout.write(JSON.stringify({
  hasAnswerCopy: Boolean(answerCopy),
  answerLabel,
  answerLabelAfterClick: answerCopy ? answerCopy.textContent : null,
  afterAnswer,
  codeCopied: copied.slice(afterAnswer.length),
  labels,
  blockCount: blocks.length,
  bodyTags: body.childNodes.map(c => c.tagName),
}));
"""


def _run_copy(raw):
    module = ("import './patch.mjs';\n"
              "import { document, Node } from './shim.mjs';\n"
              "const threadId = 'thread-test';\n"
              "const log = document.createElement('div');\n"
              + _ui_source()
              + "\nexport { addAnswer, finishAnswer, renderMarkdown, log };\n"
                "export { Node } from './shim.mjs';\n")
    return _node_run("ui_copy_", {"ui.mjs": module}, _COPY_RUNNER, raw)


_COPY_SAMPLE = "설명\n\n```python\nprint(1)\n```\n\n```\nplain\n```\n"


def test_answer_copy_button_copies_the_raw_markdown():
    """D6: 답변 하단 복사 버튼은 렌더 결과가 아니라 마크다운 원문을 복사한다."""
    result = _run_copy(_COPY_SAMPLE)

    assert result["hasAnswerCopy"] is True
    assert result["answerLabel"] == "복사"                 # 아이콘 없이 텍스트(D6)
    assert result["afterAnswer"] == [_COPY_SAMPLE]
    assert result["answerLabelAfterClick"] == "복사됨"     # 눌렀다는 피드백


def test_code_blocks_are_decorated_on_a_browser_faithful_dom():
    """D5·U4: 실제 브라우저에서도 <pre> 가 상단 바로 감싸지고 코드 복사는 그 블록만 복사한다.

    브라우저의 Element.tagName 은 대문자('PRE')다. 소문자로 비교하면 코드가 있어도
    화면에는 상단 바·언어 라벨·복사 버튼이 하나도 생기지 않는다.
    """
    result = _run_copy(_COPY_SAMPLE)

    assert result["blockCount"] == 2, (
        f"코드 블록이 장식되지 않았다(본문 자식 태그: {result['bodyTags']})"
    )
    assert result["labels"] == ["python", "code"]          # 언어 라벨 / 표기 없으면 기본값
    assert result["codeCopied"] == ["print(1)", "plain"]   # 코드 복사는 그 블록 텍스트만


def test_copy_button_label_is_created_without_html_strings():
    """복사 버튼도 textContent 로만 만든다(U8 표면 유지)."""
    body = _function_body("copyButton")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in body
    assert "createElement('button')" in body
    assert "button.textContent = label" in body


# U12·U13. 예시·빈 화면 전환을 실제로 실행한다

_EXAMPLES_RUNNER = """
import { readFileSync } from 'node:fs';
const mod = await import('./examples.mjs');
const { document, log, examples, input, state, showExamples, setExamplesDisabled, EXAMPLES } = mod;

const snap = () => ({
  show: examples.classes.has('show'),
  empty: document.body.classes.has('empty'),
});
const out = { built: [], states: [] };
for (const b of examples.childNodes) {
  out.built.push({ tag: b.childNodes[0].textContent, text: b.childNodes[1].textContent, type: b.type });
}
out.states.push(snap());                                   // 로드 직후: 로그가 비어 있다

const el = document.createElement('div');                  // 첫 질문이 들어오면
log.appendChild(el);
showExamples();
out.states.push(snap());

log.childNodes = [];                                       // 새 대화(로그 비움)
showExamples();
out.states.push(snap());

examples.childNodes[1].dispatch('click', {});              // 예시 클릭
out.clicked = { value: input.value, submits: state.submits, grows: state.grows };

setExamplesDisabled(true);
out.disabledOn = examples.childNodes.map(b => b.disabled);
setExamplesDisabled(false);
out.disabledOff = examples.childNodes.map(b => b.disabled);
out.examples = EXAMPLES;
process.stdout.write(JSON.stringify(out));
"""


def _run_examples():
    script = _script()
    constants = script[script.index("const EXAMPLES = ["):script.index("];", script.index("const EXAMPLES = [")) + 2]
    source = script[script.index("function buildExamples("):script.index("// 내용에 맞춰 높이를 늘리되")]
    assert "function showExamples()" in source and "function setExamplesDisabled(" in source
    module = ("import './patch.mjs';\n"
              "import { document, Node } from './shim.mjs';\n"
              "export const state = { submits: 0, grows: 0 };\n"
              "const log = document.createElement('div');\n"
              "const examples = document.createElement('div');\n"
              "const input = { value: '', style: {}, scrollHeight: 30 };\n"
              "const form = { requestSubmit: () => { state.submits++; } };\n"
              "const restored = Promise.resolve();\n"
              "function autoGrow() { state.grows++; }\n"
              + constants + "\n"
              + source
              + "\nexport { document, log, examples, input, showExamples, setExamplesDisabled, EXAMPLES };\n")
    return _node_run("ui_examples_", {"examples.mjs": module}, _EXAMPLES_RUNNER, {})


def test_examples_toggle_and_click_actually_work():
    result = _run_examples()

    assert len(result["built"]) == 4
    assert [b["type"] for b in result["built"]] == ["button"] * 4      # submit 이 아니다
    assert [b["tag"] for b in result["built"]] == [e["label"] for e in result["examples"]]
    assert [b["text"] for b in result["built"]] == [e["text"] for e in result["examples"]]

    empty_at_load, after_message, after_reset = result["states"]
    assert empty_at_load == {"show": True, "empty": True}
    assert after_message == {"show": False, "empty": False}            # 첫 전송 시 숨김
    assert after_reset == {"show": True, "empty": True}                # 새 대화 후 다시 표시

    assert result["clicked"]["value"] == result["examples"][1]["text"]  # 전송되는 값은 text
    assert result["clicked"]["submits"] == 1                           # 기존 submit 경로 1회
    assert result["clicked"]["grows"] == 1                             # 높이 재계산도 탄다

    assert result["disabledOn"] == [True] * 4                          # 전송 중 잠금
    assert result["disabledOff"] == [False] * 4
