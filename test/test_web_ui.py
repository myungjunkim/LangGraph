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
    assert set(calls) == {"/check", "/v1/chat/stream"}


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
    assert re.search(r"let\s+threadId\s*=\s*crypto\.randomUUID\(\)", script)


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
    super(Node.ELEMENT_NODE); this.tagName = tag; this.attrs = {};
    this.classes = new Set();
    this.classList = { add: (c) => this.classes.add(c), contains: (c) => this.classes.has(c) };
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
  if (node.tagName === 'br') return '<br>';
  const attrs = Object.entries(node.attrs).map(([k, v]) => ` ${k}="${v}"`).join('');
  return `<${node.tagName}${attrs}>${inner}</${node.tagName}>`;
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
