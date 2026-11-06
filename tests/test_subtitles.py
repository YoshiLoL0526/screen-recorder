"""Subtítulos SRT/VTT (docs/30): parser, cues a textos y export SRT."""

from __future__ import annotations

from recstudio.core import subtitles as sub


def test_parse_srt_con_bom_crlf_indices_y_multilinea() -> None:
    data = (
        "\ufeff1\r\n00:00:01,000 --> 00:00:03,500\r\nHola <i>mundo</i>\r\n\r\n"
        "2\r\n00:00:04,000 --> 00:00:05,250\r\nSegunda línea\r\ncon dos renglones\r\n"
    )
    cues = sub.parse_subtitles(data)
    assert [(c.start, c.end, c.text) for c in cues] == [
        (1.0, 3.5, "Hola mundo"),
        (4.0, 5.25, "Segunda línea con dos renglones"),
    ]


def test_parse_vtt_ignora_cabecera_note_y_ajustes() -> None:
    data = (
        "WEBVTT\nKind: captions\n\n"
        "NOTE esto no es un cue\n\n"
        "00:01.000 --> 00:02.000 align:start position:10%\n<v Ana>Buenas</v>\n\n"
        "00:03.000 --> 00:04.000\n{\\an8}Arriba\n"
    )
    cues = sub.parse_subtitles(data)
    assert [(c.start, c.end, c.text) for c in cues] == [
        (1.0, 2.0, "Buenas"),
        (3.0, 4.0, "Arriba"),
    ]


def test_parse_ignora_cues_invalidos_y_vacios() -> None:
    data = (
        "1\n00:00:05,000 --> 00:00:04,000\nAl revés\n\n"
        "2\n00:00:06,000 --> 00:00:07,000\n\n"
        "3\nsin tiempos\nTexto\n"
    )
    assert sub.parse_subtitles(data) == []


def test_cues_a_textos_recorta_y_marca_subtitulo() -> None:
    cues = [
        sub.SubtitleCue(1.0, 3.0, "uno"),
        sub.SubtitleCue(11.0, 12.0, "fuera"),
        sub.SubtitleCue(4.0, 20.0, "se recorta"),
    ]
    texts = sub.cues_to_texts(cues, total=10.0)
    assert [t.text for t in texts] == ["uno", "se recorta"]
    assert all(t.subtitle for t in texts)
    assert (texts[1].start, texts[1].end) == (4.0, 10.0)
    assert (texts[0].x, texts[0].y) == (0.5, 0.88)
    assert texts[0].bold is True


def test_format_srt_ida_y_vuelta() -> None:
    cues = [
        sub.SubtitleCue(1.25, 2.5, "Hola"),
        sub.SubtitleCue(3.0, 4.125, "Adiós"),
    ]
    texts = sub.cues_to_texts(cues)
    data = sub.format_srt(texts)
    assert "00:00:01,250 --> 00:00:02,500" in data
    assert "00:00:03,000 --> 00:00:04,125" in data
    again = sub.parse_subtitles(data)
    assert [(c.start, c.end, c.text) for c in again] == [
        (1.25, 2.5, "Hola"),
        (3.0, 4.125, "Adiós"),
    ]
