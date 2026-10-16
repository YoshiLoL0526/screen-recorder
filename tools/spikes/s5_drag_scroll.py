"""S5 (+S7) — Semántica de DragEvent/scroll/cursor y cajas absolutas dentro de un scroll.

Responde con el Pilot (sin ventana) a:
1. ¿`DragEvent.x/y` vienen en coordenadas del contenido o del viewport cuando el nodo
   está dentro de un contenedor desplazado horizontalmente?
2. ¿`position="absolute"` funciona dentro de un contenedor con scroll (se mueve con el
   contenido, recibe clics) para el patrón de asas del timeline?
3. ¿Los cursores por elemento (`cursor="col-resize"`) se aplican en hover (S7)?
4. ¿Un drag sigue llegando con el puntero fuera del nodo?

Uso:  uv run python tools/spikes/s5_drag_scroll.py
"""

from __future__ import annotations

import json

from aurora import App, Canvas, box, canvas, column, scroll, text

from _common import OUT, save_json

EVENTS: dict[str, list] = {"canvas_drag": [], "handle_drag": [], "clicks": []}


def draw(c: Canvas) -> None:
    c.clear("$surface")
    c.rect(0, 0, 200, 40, radius=6, fill="$primary")
    c.text(8, 10, "contenido x=0..200", size=12, color="#FFFFFF")


def on_canvas_drag(e) -> None:  # noqa: ANN001
    EVENTS["canvas_drag"].append({"phase": e.phase, "x": round(e.x, 1), "y": round(e.y, 1),
                                  "dx": round(e.dx, 1), "dy": round(e.dy, 1)})


def on_handle_drag(e) -> None:  # noqa: ANN001
    EVENTS["handle_drag"].append({"phase": e.phase, "x": round(e.x, 1), "y": round(e.y, 1)})


def build_view():
    return column(padding=10, gap=8)[
        text("S5 — drag/scroll", key="title", size=15, weight=700),
        scroll(axis="x", key="sc", width=400, height=140)[
            box(key="content", width=2000, height=120)[
                canvas(
                    draw,
                    key="tl",
                    width=2000,
                    height=100,
                    cursor="crosshair",
                    on_drag=on_canvas_drag,
                    on_click=lambda: EVENTS["clicks"].append("canvas"),
                ),
                box(
                    key="handle",
                    position="absolute",
                    left=600,
                    top=10,
                    width=12,
                    height=80,
                    bg="$accent",
                    bg_hover="$primary",
                    cursor="col-resize",
                    on_drag=on_handle_drag,
                    on_click=lambda: EVENTS["clicks"].append("handle"),
                ),
            ]
        ],
    ]


def main() -> None:
    app = App(build_view, title="s5", size=(460, 220))
    p = app.pilot()
    p.settle()

    report: dict = {"fases": {}, "preguntas": {}}

    rect_sc = p.rect("sc")
    rect_tl = p.rect("tl")
    rect_handle = p.rect("handle")
    print("rects iniciales:")
    print(f"  sc     = {rect_sc}")
    print(f"  tl     = {rect_tl}   (canvas de 2000 px dentro de un viewport de 400)")
    print(f"  handle = {rect_handle}  (absoluta en left=600)")

    # --- P1: drag sobre el canvas sin scroll -----------------------------------------
    px, py = rect_sc[0] + 50, rect_sc[1] + 60
    p.move_to(px, py)
    p.mouse_down()
    p.move_to(px + 60, py)
    p.move_to(px + 200, py)   # se sale del viewport a propósito
    p.mouse_up()
    p.settle()
    d0 = EVENTS["canvas_drag"]
    print(f"\nP1 drag sin scroll (pulsado en ventana x={px:.0f}): {json.dumps(d0)}")
    report["preguntas"]["P1_coords_sin_scroll"] = d0

    # --- P2: scroll de 300 px y rects -------------------------------------------------
    p.scroll("sc", dx=300)
    p.settle()
    off = p.scroll_offset("sc")
    rect_handle2 = p.rect("handle")
    rect_tl2 = p.rect("tl")
    print(f"\nP2 tras scroll dx=300: offset={off}  rect(handle)={rect_handle2}  rect(tl)={rect_tl2}")
    report["preguntas"]["P2_offset"] = off
    report["preguntas"]["P2_rect_handle_antes"] = rect_handle
    report["preguntas"]["P2_rect_handle_despues"] = rect_handle2

    # --- P3: mismo punto de ventana, ahora el contenido mapea a x=350 ------------------
    EVENTS["canvas_drag"].clear()
    p.move_to(px, py)
    p.mouse_down()
    p.move_to(px + 30, py)
    p.mouse_up()
    p.settle()
    d1 = EVENTS["canvas_drag"]
    x_start = d1[0]["x"] if d1 else None
    espacio = "contenido" if x_start is not None and x_start > 100 else "viewport-local"
    print(f"P3 drag en el mismo punto de ventana tras scroll: {json.dumps(d1)}")
    print(f"   → DragEvent.x inicial = {x_start} ⇒ coordenadas de {espacio}")
    report["preguntas"]["P3_coords_con_scroll"] = d1
    report["preguntas"]["P3_espacio"] = espacio

    # --- P4: clic sobre el asa después del scroll --------------------------------------
    EVENTS["clicks"].clear()
    cx, cy = rect_handle2[0] + rect_handle2[2] / 2, rect_handle2[1] + rect_handle2[3] / 2
    p.click_at(cx, cy)
    p.settle()
    print(f"\nP4 clic sobre el asa en ventana ({cx:.0f},{cy:.0f}): clicks={EVENTS['clicks']}")
    report["preguntas"]["P4_clicks"] = list(EVENTS["clicks"])

    # --- P5: cursores (S7) --------------------------------------------------------------
    cursor_tests = {}
    p.move_to(cx, cy)
    p.settle()
    cursor_tests["sobre_asa"] = p.cursor
    p.move_to(px, py)
    p.settle()
    cursor_tests["sobre_canvas"] = p.cursor
    p.move_to(rect_sc[0] + 10, rect_sc[1] + 10)
    p.settle()
    cursor_tests["sobre_scroll_vacio?"] = p.cursor
    print(f"\nP5 cursores: {cursor_tests}")
    report["preguntas"]["P5_cursores"] = cursor_tests

    # --- P6: hovered ----------------------------------------------------------------
    p.move_to(cx, cy)
    p.settle()
    report["preguntas"]["P6_hovered_sobre_asa"] = p.hovered
    print(f"P6 hovered sobre asa: {p.hovered}")

    p.png(str(OUT / "s5.png"))
    save_json("s5.json", report)
    print(f"\nPNG: {OUT / 's5.png'}\nGuardado: {OUT / 's5.json'}")


if __name__ == "__main__":
    main()
