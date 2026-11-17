import hashlib
import json
import time
import uuid
from datetime import datetime

import streamlit as st
from streamlit_js_eval import streamlit_js_eval

# 항목은 서버 파일이 아니라 각 사용자의 브라우저(localStorage)에 저장한다.
# 서버 파일은 배포 환경에서 재시작 시 초기화되고 모든 방문자가 공유하게 되기 때문이다.
STORAGE_KEY = "shopping_list_items"
BLOCKED = "__BLOCKED__"  # 브라우저가 localStorage 접근을 막았을 때 JS가 돌려주는 값

MAX_NAME_LEN = 50
MIN_QTY, MAX_QTY = 1, 99
QTY_OPTIONS = list(range(MIN_QTY, MAX_QTY + 1))
MAX_TOMBSTONES = 500  # 삭제 기록(다른 탭과 합칠 때 되살아나지 않게 하는 용도) 최대 보관 수
DOUBLE_SUBMIT_SEC = 1.5  # 추가 직후 이 시간 안의 빈 제출(더블클릭, 키 반복)은 경고 없이 무시


# ---------------------------------------------------------------- 데이터 ----
def new_item(name: str, quantity: int = 1) -> dict:
    """PRD 5장 데이터 모델에 맞는 새 항목을 만든다."""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": quantity,
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def clean_item(raw):
    """저장된 항목 하나를 검증·정리한다. 쓸 수 없는 항목이면 None."""
    if not isinstance(raw, dict):
        return None
    item_id, name = raw.get("id"), raw.get("name")
    quantity, checked = raw.get("quantity"), raw.get("checked")
    if not isinstance(item_id, str) or not item_id or not isinstance(name, str):
        return None
    name = name.strip()[:MAX_NAME_LEN]
    if not name:
        return None
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        return None
    if not isinstance(checked, bool):
        return None
    created_at = raw.get("created_at")
    return {
        "id": item_id,
        "name": name,
        "quantity": min(max(quantity, MIN_QTY), MAX_QTY),
        "checked": checked,
        "created_at": created_at if isinstance(created_at, str) else "",
    }


def parse_state(raw) -> tuple:
    """저장된 JSON 문자열을 (항목 리스트, 삭제된 id 리스트)로 변환한다.

    예전 형식(항목 배열)도 읽는다. 비었거나 깨진 값은 빈 상태로 처리하고,
    id 중복·삭제 기록에 있는 항목·검증을 통과하지 못한 항목은 버린다.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [], []
    if isinstance(data, list):
        data = {"items": data, "deleted": []}
    if not isinstance(data, dict):
        return [], []
    deleted = data.get("deleted")
    deleted = [d for d in deleted if isinstance(d, str)] if isinstance(deleted, list) else []
    dead, seen, items = set(deleted), set(), []
    raw_items = data.get("items")
    for raw_item in raw_items if isinstance(raw_items, list) else []:
        item = clean_item(raw_item)
        if item and item["id"] not in seen and item["id"] not in dead:
            seen.add(item["id"])
            items.append(item)
    return items, deleted[-MAX_TOMBSTONES:]


# ------------------------------------------------------------ 브라우저 저장 ----
def load_script() -> str:
    """시작할 때 한 번 실행: 화면 언어 설정, 다른 탭 변경 감지, 저장된 값 읽기."""
    key = json.dumps(STORAGE_KEY)
    return f"""(() => {{
  const K = {key};
  try {{ window.parent.document.documentElement.lang = 'ko'; }} catch (e) {{}}
  try {{
    const p = window.parent;
    if (!p.__shopSync) {{
      p.__shopSync = true;
      // 부모 창 안에서 실행되도록 eval로 등록: 다른 탭이 목록을 바꾸면 이 탭도 새로 불러온다.
      // 이 탭의 저장용 iframe이 쓴 값도 이벤트로 오기 때문에, 직접 쓴 값(__shopLastWritten)은 무시한다.
      p.eval("window.addEventListener('storage', function (e) {{ if (e.key === " + JSON.stringify(K) + " && e.newValue !== window.__shopLastWritten) location.reload(); }})");
    }}
  }} catch (e) {{}}
  try {{ return localStorage.getItem(K) ?? '[]'; }} catch (e) {{ return {json.dumps(BLOCKED)}; }}
}})()"""


def save_script(items: list, deleted: list) -> str:
    """변경이 있을 때 실행: 저장소의 최신 내용과 합쳐서 저장한다.

    다른 탭이 먼저 저장한 항목은 유지하고(삭제 기록이 있는 항목만 제외),
    같은 항목은 이 탭의 내용을 우선한다. 합친 결과가 이 탭의 내용과 다르면 그 결과를 돌려준다.
    """
    payload = json.dumps({"items": items, "deleted": deleted[-MAX_TOMBSTONES:]}, ensure_ascii=False)
    return f"""(() => {{
  const K = {json.dumps(STORAGE_KEY)};
  const mine = JSON.parse({json.dumps(payload)});
  try {{
    let cur = null;
    try {{ cur = JSON.parse(localStorage.getItem(K) || 'null'); }} catch (e) {{}}
    if (Array.isArray(cur)) cur = {{ items: cur, deleted: [] }};
    if (!cur || typeof cur !== 'object' || !Array.isArray(cur.items)) cur = {{ items: [], deleted: [] }};
    const dead = new Set([...(Array.isArray(cur.deleted) ? cur.deleted : []), ...mine.deleted]);
    const mineIds = new Set(mine.items.map(i => i.id));
    const out = mine.items.filter(i => !dead.has(i.id));
    for (const it of cur.items) {{
      if (it && typeof it.id === 'string' && !mineIds.has(it.id) && !dead.has(it.id)) out.push(it);
    }}
    const next = JSON.stringify({{ items: out, deleted: [...dead].slice(-{MAX_TOMBSTONES}) }});
    if (localStorage.getItem(K) !== next) {{
      try {{ window.parent.__shopLastWritten = next; }} catch (e) {{}}
      localStorage.setItem(K, next);
    }}
    return JSON.stringify(out) === JSON.stringify(mine.items) ? null : next;
  }} catch (e) {{ return {json.dumps(BLOCKED)}; }}
}})()"""


def sync_storage(items: list, deleted: list) -> None:
    """현재 항목을 브라우저 localStorage에 기록한다.

    내용 해시를 컴포넌트 key로 쓰기 때문에, 내용이 바뀔 때만 새로 마운트되어 JS가 실행된다.
    다른 탭의 변경과 합쳐진 결과가 돌아오면 그 내용으로 화면을 갱신한다.
    """
    digest = hashlib.md5(
        json.dumps([items, deleted], ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    result = streamlit_js_eval(js_expressions=save_script(items, deleted), key=f"ls_save_{digest}")
    if result == BLOCKED:
        st.session_state.storage_blocked = True
    elif isinstance(result, str) and result.startswith("{"):
        merged, merged_deleted = parse_state(result)
        st.session_state.shopping_items = merged
        st.session_state.deleted = merged_deleted
        st.rerun()


def init_state() -> None:
    """session_state를 초기화한다. 처음에는 브라우저 저장소에서 항목을 불러온다."""
    # 주의: "items"는 session_state(dict)의 메서드 이름과 겹치므로 쓰지 않는다.
    if "shopping_items" not in st.session_state:
        # 값이 없으면 "[]"로 돌려받아, None(= 아직 브라우저에서 응답 전)과 구분한다.
        raw = streamlit_js_eval(js_expressions=load_script(), key="ls_load")
        if raw is None:
            st.caption("불러오는 중…")
            st.stop()  # 브라우저가 응답하면 자동으로 다시 실행된다.
        blocked = raw == BLOCKED
        items, deleted = ([], []) if blocked else parse_state(raw)
        st.session_state.shopping_items = items
        st.session_state.deleted = deleted
        st.session_state.storage_blocked = blocked
    st.session_state.setdefault("editing_id", None)
    st.session_state.setdefault("confirm_clear", False)
    st.session_state.setdefault("flash", None)
    st.session_state.setdefault("undo", None)
    st.session_state.setdefault("form_error", None)
    st.session_state.setdefault("edit_error", None)
    st.session_state.setdefault("last_add_ts", 0.0)


# ------------------------------------------------------------------ 로직 ----
def flash(message: str, icon: str = "✅") -> None:
    """다음 실행 때 토스트로 보여 줄 메시지를 예약한다."""
    st.session_state.flash = (message, icon)


ESCAPE_CHARS = set("\\`*_{}[]()#+-.!|~$<>:&")


def escape_md(text: str) -> str:
    """사용자 입력이 마크다운/HTML 엔티티로 해석되지 않도록 특수문자를 이스케이프한다."""
    return "".join("\\" + ch if ch in ESCAPE_CHARS else ch for ch in text)


def find_item(item_id: str):
    """id로 항목을 찾는다. 없으면 None."""
    for item in st.session_state.shopping_items:
        if item["id"] == item_id:
            return item
    return None


def validate_name(name: str):
    """이름을 검증한다. 문제가 없으면 None, 있으면 오류 메시지를 반환한다."""
    if not name:
        return "아이템 이름을 입력해 주세요."
    if len(name) > MAX_NAME_LEN:
        return f"이름은 {MAX_NAME_LEN}자 이내로 입력해 주세요."
    return None


def clear_undo() -> None:
    """되돌리기 기록을 지운다. (다른 변경이 생기면 더 이상 되돌릴 수 없다)"""
    st.session_state.undo = None


def add_item(name: str, quantity: int) -> None:
    """항목을 리스트 맨 아래에 추가한다. 같은 이름이 있으면 경고를 예약한다."""
    clear_undo()
    items = st.session_state.shopping_items
    duplicate = any(i["name"].casefold() == name.casefold() for i in items)
    items.append(new_item(name, quantity))
    safe = escape_md(name)
    if duplicate:
        flash(f"'{safe}'은(는) 이미 리스트에 있어요. 그래도 추가했어요.", "⚠️")
    else:
        flash(f"'{safe}'을(를) 추가했어요.")


def submit_add() -> None:
    """추가 폼 제출 콜백."""
    name = str(st.session_state.get("add_name", "")).strip()
    quantity = int(st.session_state.get("add_qty", MIN_QTY))
    error = validate_name(name)
    if error:
        # 더블클릭·키 반복으로 방금 추가한 직후에 들어온 빈 제출은 조용히 무시한다.
        if time.time() - st.session_state.last_add_ts > DOUBLE_SUBMIT_SEC:
            st.session_state.form_error = error
        return
    add_item(name, quantity)
    st.session_state.last_add_ts = time.time()


def update_item(item_id: str, name: str, quantity: int) -> None:
    """항목의 이름과 수량을 수정한다."""
    clear_undo()
    item = find_item(item_id)
    if item:
        item["name"] = name
        item["quantity"] = quantity
    st.session_state.editing_id = None
    flash("수정했어요.")


def submit_edit(item_id: str) -> None:
    """수정 폼 저장 콜백."""
    name = str(st.session_state.get(f"edit_name_{item_id}", "")).strip()
    quantity = int(st.session_state.get(f"edit_qty_{item_id}", MIN_QTY))
    error = validate_name(name)
    if error:
        st.session_state.edit_error = error
        return
    update_item(item_id, name, quantity)


def delete_item(item_id: str) -> None:
    """항목을 삭제하고 되돌리기 정보를 남긴다."""
    items = st.session_state.shopping_items
    for index, item in enumerate(items):
        if item["id"] == item_id:
            break
    else:
        return
    del items[index]
    st.session_state.deleted.append(item_id)
    if st.session_state.editing_id == item_id:
        st.session_state.editing_id = None
    st.session_state.undo = {
        "entries": [(index, item)],
        "message": f"'{escape_md(item['name'])}'을(를) 삭제했어요.",
    }


def toggle_item(item_id: str) -> None:
    """체크박스 상태를 항목에 반영한다. (체크박스 on_change 콜백)"""
    clear_undo()
    item = find_item(item_id)
    if item:
        item["checked"] = bool(st.session_state.get(f"check_{item_id}"))


def start_edit(item_id: str) -> None:
    """해당 항목을 수정 모드로 전환한다. (동시에 하나만 수정 가능)"""
    clear_undo()
    st.session_state.edit_error = None
    st.session_state.editing_id = item_id


def cancel_edit() -> None:
    """수정 모드를 취소한다."""
    st.session_state.edit_error = None
    st.session_state.editing_id = None


def clear_checked() -> None:
    """체크된 항목을 모두 삭제하고 되돌리기 정보를 남긴다."""
    items = st.session_state.shopping_items
    entries = [(i, item) for i, item in enumerate(items) if item["checked"]]
    st.session_state.shopping_items = [item for item in items if not item["checked"]]
    st.session_state.deleted.extend(item["id"] for _, item in entries)
    st.session_state.confirm_clear = False
    if st.session_state.editing_id and not find_item(st.session_state.editing_id):
        st.session_state.editing_id = None
    st.session_state.undo = {
        "entries": entries,
        "message": f"완료한 항목 {len(entries)}개를 삭제했어요.",
    }


def undo_delete() -> None:
    """마지막 삭제를 원래 위치로 되돌린다.

    다른 탭과 합칠 때 삭제 기록 때문에 다시 지워지지 않도록 새 id를 부여한다.
    """
    undo = st.session_state.undo
    if not undo:
        return
    items = st.session_state.shopping_items
    for index, item in sorted(undo["entries"], key=lambda e: e[0]):
        items.insert(min(index, len(items)), dict(item, id=str(uuid.uuid4())))
    st.session_state.undo = None
    flash("되돌렸어요.", "↩️")


def request_clear() -> None:
    clear_undo()
    st.session_state.confirm_clear = True


def cancel_clear() -> None:
    st.session_state.confirm_clear = False


# ------------------------------------------------------------------- 화면 ----
ACCENT = "#2E9E6B"  # .streamlit/config.toml의 primaryColor와 같은 값

CSS = f"""
<style>
/* 상단 여백을 줄여 한 화면에 더 많이 보이게 한다 */
[data-testid="stMainBlockContainer"] {{ padding-top: 2.5rem; padding-bottom: 3rem; }}
h1 {{ padding-bottom: 0.25rem; }}
@media (max-width: 480px) {{
    [data-testid="stMainBlockContainer"] h1 {{ font-size: 1.9rem !important; line-height: 1.3; }}
    [data-testid="stMainBlockContainer"] {{ padding-top: 1.5rem; }}
}}

/* 항목 카드 */
[class*="st-key-row_"], [class*="st-key-editrow_"], .st-key-undo_bar {{
    border-radius: 12px;
    padding: 0.15rem 0.5rem;
    flex-wrap: nowrap;            /* 모바일에서도 한 줄 유지 */
    align-items: center;
}}
[class*="st-key-row_"] > div {{ min-width: 0; }}

/* 체크박스: 이름 오른쪽 빈 곳까지 눌리도록 행 전체로 넓히고, 터치 영역을 44px 이상으로 */
[class*="st-key-row_"] [data-testid="stElementContainer"]:has([data-testid="stCheckbox"]) {{
    flex: 1 1 auto; align-self: stretch; display: flex;
}}
[class*="st-key-row_"] [data-testid="stCheckbox"] {{ width: 100%; min-width: 0; display: flex; }}
[class*="st-key-row_"] [data-testid="stCheckbox"] label {{
    width: 100%; min-height: 44px; align-items: center; cursor: pointer;
}}
[class*="st-key-row_"] [data-testid="stCheckbox"] p {{ overflow-wrap: anywhere; }}

/* 수정/삭제 버튼: 44px 터치 영역, 삭제는 간격을 더 둬서 잘못 누르지 않게 한다 */
[class*="st-key-row_"] [data-testid="stBaseButton-tertiary"] {{ min-width: 44px; min-height: 44px; padding: 0; }}
[class*="st-key-row_"] [data-testid^="stElementContainer"]:last-child {{ margin-left: 0.5rem; }}
/* 아이콘만 보이게 하되 글자는 화면낭독기용 이름으로 남긴다 ("/ ''"는 아이콘을 읽지 않게 하는 대체 텍스트) */
[class*="st-key-row_"] [data-testid="stBaseButton-tertiary"] p {{
    position: absolute; width: 1px; height: 1px; overflow: hidden;
    clip: rect(0, 0, 0, 0); white-space: nowrap;
}}
[class*="st-key-edit_"] button::before {{ content: "✏️" / ""; font-size: 1.15rem; }}
[class*="st-key-del_"] button::before {{ content: "🗑️" / ""; font-size: 1.15rem; }}

/* 완료된 항목: 취소선 + 흐리게 */
[class*="st-key-row_done_"] [data-testid="stCheckbox"] p {{ text-decoration: line-through; opacity: 0.5; }}
[class*="st-key-row_done_"] .qty-badge {{ opacity: 0.5; }}

/* 수정 중인 카드 강조 */
[class*="st-key-editrow_"] {{ border: 2px solid {ACCENT}; }}

/* 수량 배지 */
.qty-badge {{
    background: rgba(128, 128, 128, 0.18);
    border-radius: 999px;
    padding: 0.1rem 0.65rem;
    font-weight: 600;
    font-size: 0.85rem;
    white-space: nowrap;
}}

/* 빈 상태 */
.empty-state {{ text-align: center; padding: 2.5rem 0; opacity: 0.75; line-height: 2; }}
.empty-state .icon {{ font-size: 3rem; }}

/* streamlit-js-eval 컴포넌트가 만드는 빈 공간 제거 */
[data-testid="stElementContainer"]:has(iframe[title*="streamlit_js_eval"]) {{
    position: absolute; width: 0; height: 0; overflow: hidden; margin: 0;
}}
</style>
"""

FILTERS = ["전체", "미완료", "완료"]


def render_progress(items: list) -> None:
    """전체 N개 중 M개 완료와 진행 바를 표시한다. 항목이 없으면 숨긴다."""
    if not items:
        return
    done = sum(1 for i in items if i["checked"])
    st.write(f"전체 {len(items)}개 중 {done}개 완료")
    st.progress(done / len(items))


def render_add_form() -> None:
    """아이템 추가 폼. 이름은 넓게, 수량과 추가 버튼은 오른쪽에 둔다."""
    with st.form("add_form", clear_on_submit=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.text_input(
                "아이템 이름",
                key="add_name",
                max_chars=MAX_NAME_LEN,
                placeholder="사야 할 물건을 입력하세요",
                label_visibility="collapsed",
            )
            # number_input은 폼 안에서 Enter로 제출하면 방금 입력한 값이 무시되는 문제가 있어 selectbox를 쓴다.
            st.selectbox(
                "수량", QTY_OPTIONS, index=0, key="add_qty",
                label_visibility="collapsed", width=100,
            )
            st.form_submit_button("추가", type="primary", on_click=submit_add)
    error = st.session_state.form_error
    if error:
        st.session_state.form_error = None
        st.warning(error)


def render_edit_row(item: dict) -> None:
    """수정 모드의 항목 카드. 눈에 띄게 강조하고 저장/취소 버튼을 제공한다."""
    item_id = item["id"]
    with st.container(border=True, key=f"editrow_{item_id}"):
        with st.form(f"edit_form_{item_id}", border=False):
            st.text_input(
                "이름", value=item["name"], key=f"edit_name_{item_id}", max_chars=MAX_NAME_LEN
            )
            with st.container(horizontal=True, vertical_alignment="bottom"):
                st.selectbox(
                    "수량", QTY_OPTIONS, index=QTY_OPTIONS.index(item["quantity"]),
                    key=f"edit_qty_{item_id}", width=120,
                )
                st.form_submit_button(
                    "저장", type="primary", on_click=submit_edit, args=(item_id,)
                )
                st.form_submit_button("취소", on_click=cancel_edit)
        error = st.session_state.edit_error
        if error:
            st.warning(error)


def render_item_row(item: dict) -> None:
    """일반 모드의 항목 카드: 체크박스(이름) / 수량 배지 / 수정 / 삭제."""
    item_id = item["id"]
    # 완료 여부를 컨테이너 key에 넣어 CSS로 취소선/흐림을 적용한다. (라벨에 마크다운을 쓰지 않는다)
    row_key = f"row_done_{item_id}" if item["checked"] else f"row_{item_id}"
    with st.container(
        border=True, key=row_key, horizontal=True, vertical_alignment="center", gap="xsmall"
    ):
        st.checkbox(
            escape_md(item["name"]),
            value=item["checked"],
            key=f"check_{item_id}",
            on_change=toggle_item,
            args=(item_id,),
            width="stretch",
        )
        st.markdown(
            f'<span class="qty-badge">×{int(item["quantity"])}</span>',
            unsafe_allow_html=True,
            width="content",
        )
        # 아이콘은 CSS로 그리고, 버튼 이름은 한글 텍스트("수정"/"삭제")로 남겨 화면낭독기가 읽게 한다.
        st.button(
            "수정", key=f"edit_{item_id}", help="수정",
            type="tertiary", on_click=start_edit, args=(item_id,),
        )
        st.button(
            "삭제", key=f"del_{item_id}", help="삭제",
            type="tertiary", on_click=delete_item, args=(item_id,),
        )


def render_undo() -> None:
    """삭제 직후 되돌리기 안내."""
    undo = st.session_state.undo
    if not undo:
        return
    with st.container(border=True, key="undo_bar", horizontal=True, vertical_alignment="center"):
        st.markdown(undo["message"], width="stretch")
        st.button("되돌리기", key="undo_btn", on_click=undo_delete)


def render_clear_checked(items: list) -> None:
    """완료한 항목 일괄 삭제. 실수 방지를 위해 확인 단계를 둔다."""
    done = sum(1 for i in items if i["checked"])
    if not done:
        st.session_state.confirm_clear = False
        return
    if st.session_state.confirm_clear:
        st.warning(f"완료한 항목 {done}개를 모두 삭제할까요?")
        with st.container(horizontal=True):
            st.button("삭제 확인", key="clear_yes", type="primary", on_click=clear_checked)
            st.button("취소", key="clear_no", on_click=cancel_clear)
    else:
        st.button("완료한 항목 모두 삭제", key="clear_req", on_click=request_clear)


def render_empty(message: str, icon: str = "🧺") -> None:
    """가운데 정렬된 빈 상태 안내."""
    st.markdown(
        f'<div class="empty-state"><div class="icon">{icon}</div>{message}</div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")
    init_state()
    st.markdown(CSS, unsafe_allow_html=True)

    st.title("🛒 쇼핑 리스트")

    pending = st.session_state.flash
    if pending:
        st.session_state.flash = None
        st.toast(pending[0], icon=pending[1])

    if st.session_state.storage_blocked:
        st.warning(
            "이 브라우저에서는 저장 기능이 막혀 있어요. "
            "목록은 지금 화면에서만 유지되고, 새로고침하거나 닫으면 사라져요."
        )

    items = st.session_state.shopping_items
    render_progress(items)
    render_add_form()
    render_undo()

    if not items:
        render_empty("아직 항목이 없어요. 첫 아이템을 추가해 보세요!")
    else:
        view = st.segmented_control(
            "보기", FILTERS, default="전체", key="view_filter",
            label_visibility="collapsed", on_change=cancel_edit,
        )
        if view == "미완료":
            visible = [i for i in items if not i["checked"]]
        elif view == "완료":
            visible = [i for i in items if i["checked"]]
        else:
            visible = items

        if not visible:
            render_empty(f"'{view}' 항목이 없어요.", "🔍")
        for item in visible:
            if item["id"] == st.session_state.editing_id:
                render_edit_row(item)
            else:
                render_item_row(item)

        render_clear_checked(items)

    st.caption("🔒 목록은 이 브라우저에만 저장돼요. 다른 기기나 브라우저와는 공유되지 않아요.")

    # 항상 마지막에 호출: 이번 실행에서 바뀐 내용을 브라우저에 저장한다.
    if not st.session_state.storage_blocked:
        sync_storage(st.session_state.shopping_items, st.session_state.deleted)


if __name__ == "__main__":
    main()
