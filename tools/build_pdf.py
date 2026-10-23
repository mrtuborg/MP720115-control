#!/usr/bin/env python3
"""Build the English mp72-control user guide without network access."""

from __future__ import annotations

import csv
from html import escape
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "mp72-control-guide.pdf"
SCREENSHOT = ROOT / "docs" / "examples" / "live_screen_test.png"
WAVEFORM = ROOT / "docs" / "examples" / "live_waveform_ch1.csv"

BLUE = colors.HexColor("#1666A8")
DARK = colors.HexColor("#17324D")
CYAN = colors.HexColor("#00A6B2")
PALE = colors.HexColor("#EAF4FB")
PALE_GREEN = colors.HexColor("#EAF7F0")
PALE_YELLOW = colors.HexColor("#FFF7DA")
GRAY = colors.HexColor("#657786")
LIGHT_GRAY = colors.HexColor("#F3F5F7")


def register_fonts() -> None:
    base = Path("/System/Library/Fonts/Supplemental")
    pdfmetrics.registerFont(TTFont("GuideSans", str(base / "Arial.ttf")))
    pdfmetrics.registerFont(TTFont("GuideSans-Bold", str(base / "Arial Bold.ttf")))
    pdfmetrics.registerFont(TTFont("GuideSans-Italic", str(base / "Arial Italic.ttf")))
    pdfmetrics.registerFontFamily(
        "GuideSans", normal="GuideSans", bold="GuideSans-Bold",
        italic="GuideSans-Italic", boldItalic="GuideSans-Bold",
    )


def styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title", parent=base["Title"], fontName="GuideSans-Bold",
            fontSize=27, leading=31, textColor=DARK, alignment=TA_LEFT,
            spaceAfter=10 * mm,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"], fontName="GuideSans",
            fontSize=13, leading=18, textColor=GRAY, spaceAfter=5 * mm,
        ),
        "h1": ParagraphStyle(
            "H1", parent=base["Heading1"], fontName="GuideSans-Bold",
            fontSize=18, leading=22, textColor=DARK, spaceBefore=2 * mm,
            spaceAfter=4 * mm,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontName="GuideSans-Bold",
            fontSize=12.5, leading=16, textColor=BLUE, spaceBefore=4 * mm,
            spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontName="GuideSans",
            fontSize=9.2, leading=13.2, textColor=colors.HexColor("#202A33"),
            spaceAfter=2.2 * mm,
        ),
        "small": ParagraphStyle(
            "Small", parent=base["BodyText"], fontName="GuideSans",
            fontSize=7.8, leading=10.5, textColor=GRAY, spaceAfter=1.5 * mm,
        ),
        "code": ParagraphStyle(
            "Code", parent=base["Code"], fontName="Courier", fontSize=7.2,
            leading=9.2, leftIndent=3 * mm, rightIndent=3 * mm,
            borderColor=colors.HexColor("#D7E0E8"), borderWidth=0.5,
            borderPadding=3 * mm, backColor=LIGHT_GRAY, spaceBefore=2 * mm,
            spaceAfter=3 * mm,
        ),
        "caption": ParagraphStyle(
            "Caption", parent=base["BodyText"], fontName="GuideSans-Italic",
            fontSize=7.5, leading=10, textColor=GRAY, alignment=TA_CENTER,
            spaceBefore=1 * mm, spaceAfter=3 * mm,
        ),
        "cover_label": ParagraphStyle(
            "CoverLabel", parent=base["Normal"], fontName="GuideSans-Bold",
            fontSize=9, leading=12, textColor=BLUE,
        ),
        "cover_value": ParagraphStyle(
            "CoverValue", parent=base["Normal"], fontName="GuideSans",
            fontSize=9, leading=12, textColor=DARK,
        ),
    }


def p(text: str, style) -> Paragraph:
    return Paragraph(text, style)


def code(text: str, style) -> Preformatted:
    return Preformatted(text.strip("\n"), style)


def callout(title: str, text: str, st, color=PALE) -> Table:
    table = Table(
        [[p(title, st["cover_label"])], [p(text, st["body"])]],
        colWidths=[166 * mm],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("BOX", (0, 0), (-1, -1), 0.8, BLUE),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
    ]))
    return table


def bullet(text: str, st) -> Paragraph:
    return Paragraph(f"- {text}", ParagraphStyle(
        "Bullet", parent=st["body"], leftIndent=5 * mm,
        firstLineIndent=-3.5 * mm, spaceAfter=1.2 * mm,
    ))


def make_table(rows, widths, st, header=True, font_size=7.2):
    cooked = []
    for row_index, row in enumerate(rows):
        row_style = ParagraphStyle(
            f"Cell{row_index}", parent=st["small"], fontSize=font_size,
            leading=font_size + 2,
            textColor=colors.white if header and row_index == 0 else DARK,
        )
        cooked.append([
            p(escape(str(cell)).replace("\n", "<br/>"), row_style) for cell in row
        ])
    table = Table(cooked, colWidths=widths, repeatRows=1 if header else 0)
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5DE")),
        ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6 * mm),
    ]
    if header:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), DARK))
    for row_index in range(1 if header else 0, len(rows)):
        if row_index % 2 == 0:
            commands.append(("BACKGROUND", (0, row_index), (-1, row_index), LIGHT_GRAY))
    table.setStyle(TableStyle(commands))
    return table


def waveform_drawing() -> Drawing:
    with WAVEFORM.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    xs = [float(row["time_s"]) * 1e6 for row in rows]
    ys = [float(row["volts_provisional"]) for row in rows]
    width, height = 460, 145
    left, bottom, right, top = 45, 25, 12, 18
    plot_w, plot_h = width - left - right, height - bottom - top
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if ymin == ymax:
        ymin -= 0.02
        ymax += 0.02
    points = [
        (left + (x - xmin) / (xmax - xmin) * plot_w,
         bottom + (y - ymin) / (ymax - ymin) * plot_h)
        for x, y in zip(xs, ys)
    ]
    drawing = Drawing(width, height)
    drawing.add(Rect(left, bottom, plot_w, plot_h, fillColor=colors.white,
                     strokeColor=colors.HexColor("#C8D2DC")))
    for index in range(1, 5):
        y = bottom + index * plot_h / 5
        drawing.add(Line(left, y, left + plot_w, y,
                         strokeColor=colors.HexColor("#E8EDF2"), strokeWidth=0.5))
    drawing.add(PolyLine(points, strokeColor=CYAN, strokeWidth=1.4))
    drawing.add(String(left, 6, f"{xmin:.3f} us", fontName="GuideSans", fontSize=7, fillColor=GRAY))
    drawing.add(String(left + plot_w - 36, 6, f"{xmax:.3f} us", fontName="GuideSans", fontSize=7, fillColor=GRAY))
    drawing.add(String(3, bottom + plot_h - 2, f"{ymax:.3f} V", fontName="GuideSans", fontSize=7, fillColor=GRAY))
    drawing.add(String(3, bottom, f"{ymin:.3f} V", fontName="GuideSans", fontSize=7, fillColor=GRAY))
    drawing.add(String(left + 130, height - 11, "CH1 live sample - provisional volts",
                       fontName="GuideSans-Bold", fontSize=8, fillColor=DARK))
    return drawing


def footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D7E0E8"))
    canvas.line(22 * mm, 15 * mm, 188 * mm, 15 * mm)
    canvas.setFont("GuideSans", 7.5)
    canvas.setFillColor(GRAY)
    canvas.drawString(22 * mm, 10 * mm, "mp72-control | MP720115 Ethernet/VXI-11")
    canvas.drawRightString(188 * mm, 10 * mm, str(doc.page))
    canvas.restoreState()


def build_story(st):
    story = [
        Spacer(1, 22 * mm),
        p("mp72-control", st["title"]),
        p("Multicomp Pro MP720115 control over Ethernet/VXI-11 and SCPI", st["subtitle"]),
    ]
    cover_rows = [
        [p("Tested instrument", st["cover_label"]), p("UNI-T,MP720115 / firmware 5.00.0.19", st["cover_value"])],
        [p("Connection", st["cover_label"]), p("TCPIP::<host>::inst0::INSTR, PyVISA + pyvisa-py", st["cover_value"])],
        [p("Capabilities", st["cover_label"]), p("Channels, timebase, acquisition, EDGE trigger, measurements, waveform, and screen", st["cover_value"])],
        [p("Status", st["cover_label"]), p("Live-verified on 192.168.32.132; USB-B not characterized", st["cover_value"])],
        [p("Document version", st["cover_label"]), p("October 23, 2026", st["cover_value"])],
    ]
    cover = Table(cover_rows, colWidths=[42 * mm, 124 * mm])
    cover.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), 0.8, BLUE),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CAD9E5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5 * mm),
    ]))
    story += [Spacer(1, 5 * mm), cover, Spacer(1, 12 * mm), callout(
        "Short answer",
        "Python on macOS or Linux can control this instrument and retrieve waveforms without a Windows application. "
        "The confirmed path is Ethernet/VXI-11. No universal SCPI command lists every capability; this project's allowlist combines the official manual with live verification.",
        st, PALE_GREEN,
    ), Spacer(1, 20 * mm), p("Project: ~/ws/mp72-control", st["small"]),
        p("Building this guide does not contact the instrument or network.", st["small"]), PageBreak()]

    story += [
        p("1. Purpose and boundaries", st["h1"]),
        p("The project provides the <b>mp72_control</b> library, the <b>mp72-control</b> CLI, and bounded examples for laboratory automation.", st["body"]),
        p("The instrument answers <b>*IDN?</b> with <b>UNI-T,MP720115,0000000000000,5.00.0.19</b>. This proves UNI-T firmware identity and SCPI-dialect compatibility, but not an exact UPO OEM model.", st["body"]),
        p("What the instrument can report", st["h2"]),
        bullet("identity and firmware through *IDN? and :SYSTem:VERSion?;", st),
        bullet("current values through specific documented queries;", st),
        bullet("waveform data, preamble, scalar measurements, and a screen image.", st),
        p("What it cannot report", st["h2"]),
        p("The manual documents no HELP? or COMMANDS? catalog. The project never probes commands blindly; it exposes a fixed allowlist grounded in the official manual and real-device testing.", st["body"]),
        callout("Safe by default", "No raw-SCPI pass-through, reset, clear, calibration, storage deletion, LAN setters, auto-setup, service, or telnet commands. Values are validated before transmission.", st, PALE_YELLOW),
        p("Confirmed transport", st["h2"]),
        p("The PyVISA resource is <b>TCPIP0::IP::inst0::INSTR</b>. pyvisa-py requires no NI-VISA installation. Raw TCP/5025, HiSLIP/4880, and HTTP/80 did not answer in the tested state; USB-B was not characterized.", st["body"]), PageBreak(),
    ]

    story += [
        p("2. Installation and quick start", st["h1"]),
        p("Python 3.9 or newer is required. Use a dedicated virtual environment.", st["body"]),
        code("""
cd ~/ws/mp72-control
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
pip install -e ".[plot]"   # optional NPZ and PNG support
""", st["code"]),
        p("Read-only connection check", st["h2"]),
        code("mp72-control --host 192.168.32.132 status", st["code"]),
        p("Minimal CH1 capture", st["h2"]),
        code("""
mp72-control --host 192.168.32.132 waveform \\
  --channel 1 --points 120 --format BYTE \\
  --csv waveform.csv --png waveform.png
""", st["code"]),
        p("Instrument screen", st["h2"]),
        code("mp72-control --host 192.168.32.132 screen screen.bmp", st["code"]),
        callout("Important", "The channel must be enabled. On firmware 5.00.0.19, requesting waveform data from a disabled channel times out. The library checks DISPlay? before changing transfer state.", st, PALE_YELLOW),
        p("Offline project verification", st["h2"]),
        code("python3 -m pytest -q", st["code"]), PageBreak(),
    ]

    story += [
        p("3. Configure, trigger, and capture", st["h1"]),
        p("The workflow below makes every state change explicit and never invokes auto-setup.", st["body"]),
        code("""
# 1. Channel
mp72-control --host 192.168.32.132 channel-set 1 \\
  --on --coupling DC --probe 10X --scale 0.5 --offset 0

# 2. Horizontal scale
mp72-control --host 192.168.32.132 timebase \\
  --mode MAIN --scale 1e-6 --offset 0

# 3. Acquisition processing
mp72-control --host 192.168.32.132 acquire \\
  --type AVERAGE --average-count 16 --memory-depth AUTO

# 4. EDGE trigger
mp72-control --host 192.168.32.132 trigger-edge \\
  --select --source CHANNEL1 --slope POSITIVE \\
  --level 0.2 --sweep AUTO --coupling DC

# 5. Capture
mp72-control --host 192.168.32.132 waveform \\
  --channel 1 --csv event.csv
""", st["code"]),
        p("For a repeatable frozen snapshot, select AUTO or NORMAL trigger, issue stop, capture, then issue run. SINGLE should stop after an event, but the manual provides no RUN/STOP state query, so command delivery alone is not proof.", st["body"]),
        p("Safe Python example", st["h2"]),
        code("""
from mp72_control import MP720115

with MP720115.connect_visa("192.168.32.132") as scope:
    print(scope.idn())
    data = scope.capture_waveform(1, points=120, fmt="BYTE")
    print(len(data.raw_codes), data.preamble.x_increment)
""", st["code"]),
        callout("Restoration", "capture_waveform saves WAVeform:SOURce, FORMat, and POINts, then restores them after success or error. Raw VXI-11 receives one reconnect attempt before restoration if the link is poisoned.", st), PageBreak(),
    ]

    command_rows = [
        ["Command", "Purpose", "Mutates"],
        ["status", "identity, firmware, channels, timebase, acquisition, trigger", "no"],
        ["channel-get N", "display, coupling, scale, offset, probe, bandwidth", "no"],
        ["channel-set N", "visibility, coupling, V/div, offset, probe, bandwidth", "yes"],
        ["timebase", "MAIN/WINDOW, s/div, horizontal offset", "yes"],
        ["acquire", "type, averages, memory depth, documented mode write", "yes"],
        ["trigger-edge", "source, slope, sweep, coupling, verified level", "yes"],
        ["run / stop", "start or stop acquisition; no state readback", "yes"],
        ["measure", "VPP, VMAX, VMIN, VRMS, frequency, period, and more", "no"],
        ["waveform", "BYTE/WORD/ASCII; CSV/NPZ/PNG", "temporary transfer state"],
        ["screen FILE", "current-screen BMP", "no"],
    ]
    story += [
        p("4. Interface reference", st["h1"]),
        make_table(command_rows, [31 * mm, 96 * mm, 39 * mm], st, font_size=7.5),
        p("Key enumerations", st["h2"]),
        make_table([
            ["Area", "Values"],
            ["Acquisition", "NORMAL, AVERAGE, PEAKDETECT, ENVELOPE, HRESOLUTION"],
            ["Memory", "AUTO, 700, 7K, 70K, 700K, 7M, 70M"],
            ["Trigger source", "CHANNEL1..4, EXT, EXT5, ACLINE"],
            ["Slope / sweep", "POSITIVE, NEGATIVE, ALTERNATION / AUTO, NORMAL, SINGLE"],
            ["Coupling", "channel DC/AC/GND; trigger DC/AC/LF/HF/NOISE"],
            ["Waveform", "BYTE, WORD, ASCII; --raw-only disables voltage conversion"],
        ], [42 * mm, 124 * mm], st, font_size=7.7),
        p("Acquisition mode limitation", st["h2"]),
        p("REALTIME/EQUIVALENTTIME writes are documented, but :ACQuire:MODE? times out. The CLI can send an explicit mode request, but cannot verify or restore it. Automated workflows should leave it unchanged.", st["body"]),
        p("Verified writes", st["h2"]),
        p("Firmware 5.00.0.19 accepted average count only under TYPE=AVERAGE. The controller temporarily selects AVERAGE, writes and reads count, returns to the target type, then applies memory depth. TYPE/count/depth mismatch exits with code 1. Trigger level is also applied last and verified.", st["body"]), PageBreak(),
    ]

    story += [
        p("5. Waveform format and scaling", st["h1"]),
        p("DATA? returns a definite-length IEEE 488.2 block: <b>#&lt;n&gt;&lt;length&gt;&lt;payload&gt;</b>. The library does not trust one VXI-11 fragment; it continues until the declared payload length is complete.", st["body"]),
        p("Preamble fields", st["h2"]),
        make_table([
            ["Field", "Meaning"],
            ["Format / Type", "BYTE/WORD/ASCII and acquisition type"],
            ["Points / Count", "reported points and acquisition count"],
            ["Xinc / Xor / Xref", "time step, origin, and reference"],
            ["Yinc / Yor / Yref", "volts/code, origin, and ADC reference"],
        ], [45 * mm, 121 * mm], st, font_size=8),
        p("Conversion", st["h2"]),
        code("""
time[i] = (i - x_reference) * x_increment + x_origin
volts[i] = (raw[i] - y_reference) * y_increment - y_origin
""", st["code"]),
        callout("Provisional voltage", "The y_origin sign was checked with offsets 0, 0.12, and 0.24 V. Absolute accuracy was not compared with a calibrated source, so the field remains volts_provisional. Use --raw-only for metrology until calibration.", st, PALE_YELLOW),
        waveform_drawing(),
        p("A real CH1 CSV captured through PyVISA. This waveform is nearly constant; the chart demonstrates format, not measurement accuracy.", st["caption"]),
        p("Returned points", st["h2"]),
        p("Requests for 50, 120, and 500 points all returned 697 bytes. Always use returned_points and payload length instead of requested_points. POINts can drift during RUN.", st["body"]), PageBreak(),
    ]

    story += [
        p("6. Transport and firmware behavior", st["h1"]),
        p("Firmware 5.00.0.19 sets VXI-11 END on every 1024-byte fragment. pyvisa-py read_raw therefore stopped after the first screen fragment. The controller parses the IEEE header and calls read_bytes for the exact remainder. A complete screen BMP is 1,152,054 bytes.", st["body"]),
        p("Observed behavior", st["h2"]),
        bullet(":ACQuire:MODE? does not answer; status deliberately omits it.", st),
        bullet("Average count was accepted only under AVERAGE.", st),
        bullet("MEMory:DEPTh 7K read back as AUTO; PEAKDETECT AUTO appeared as 28K.", st),
        bullet("A 0.8 V trigger-level restore read back as -11.84 V, then +8.16 V.", st),
        bullet(":WAVeform:POINts does not constrain actual DATA? payload size.", st),
        bullet("DATA? on a disabled channel times out and poisons the link.", st),
        bullet("After timeout, close the old link; queued responses can be shifted.", st),
        bullet("create_link device error 3 means an occupied owner or link slot.", st),
        bullet(":DISPlay:GRID:MODE? does not answer, so the field is never changed.", st),
        bullet("FUNCtion can change AUTO memory depth to 28K as a side effect.", st),
        bullet("FILTER high=2000 Hz read back as 1500 Hz and was rejected.", st),
        bullet("FREQ/PER/RIS/FALL return literal * when unavailable.", st),
        p("Architecture", st["h2"]),
        make_table([
            ["Layer", "Responsibility"],
            ["CLI", "arguments, operation allowlist, CSV/NPZ/PNG/BMP"],
            ["MP720115", "validation, commands, capture, restoration"],
            ["scpi_blocks", "IEEE 488.2 and ten-field preamble"],
            ["PyVISA", "primary VXI-11 transport"],
            ["raw_vxi11", "dependency-free fallback and reconnect"],
        ], [45 * mm, 121 * mm], st, font_size=8),
        p("Snapshots, not streaming", st["h2"]),
        p("The project implements sequential captures. Sustained refresh rate has not been benchmarked. Use bounded polling and avoid opening a new VXI-11 link for every sample.", st["body"]), PageBreak(),
    ]

    story += [
        p("7. Visual demonstrations", st["h1"]),
        p("<b>visual_screensaver.py</b> cycles display, FFT, MATH, XY, and FILTER without changing channels, timebase, trigger, or RUN/STOP. It snapshots readable state, verifies every write and fresh screen, then restores.", st["body"]),
        p("Verified demo timing", st["h2"]),
        p("--dwell sets the minimum display time before screen verification. Default is 2 seconds. Writes, readback, fresh-link reconnect, and screen transfer add overhead. With CH1/CH2 enabled, the tested unit runs 25 supported frames.", st["body"]),
        code("""
python examples/visual_screensaver.py --cycles 1 --dwell 5
python examples/visual_screensaver.py --cycles 0 --dwell 0.5
""", st["code"]),
        p("No BMP persistence by default", st["h2"]),
        p("With --retain-screens 0, screens are parsed, hashed, and compared in memory while logs retain SHA-256 and pixel differences. --retain-screens 3 keeps the baseline and three latest frames.", st["body"]),
        p("Cursor demo", st["h2"]),
        p("<b>cursor_demo.py</b> uses only CURSor:MODE/TYPE/SOURce/CURA/CURB. TIME coordinates are 0..699 and AMPLITUDE coordinates are 28..227. The manual has no numeric delta-result query, so the demo verifies state, coordinates, and pixels only.", st["body"]),
        code("python examples/cursor_demo.py --cycles 2 --dwell 4 --seed 720115", st["code"]),
        p("Fast cinematic show", st["h2"]),
        p("<b>cinematic_show.py</b> animates CH1-CH4 lamps, EDGE trigger, relative timebase zoom, FFT/MATH/XY/FILTER, and cursors. Frames deliberately omit readback and are labeled SENT. Startup baseline and final restoration remain verified; trigger level and RUN/STOP are never changed.", st["body"]),
        callout("Recovery contract", "Stop infinite demos with Ctrl-C and wait for status=restored. If recovery remains armed after a crash, confirm that the old VXI-11 owner is gone, then run the matching --restore PATH once. Success requires restore_report.ok=true.", st, PALE_GREEN), PageBreak(),
    ]

    story += [
        p("8. Troubleshooting", st["h1"]),
        make_table([
            ["Symptom", "Action"],
            ["VXI-11 will not open", "Check the IP address, TCP/111 reachability, and instrument LAN setup."],
            ["waveform: channel disabled", "Enable it explicitly with channel-set N --on."],
            ["Responses shift after timeout", "Close the old link immediately and open a fresh one."],
            ["create_link error 3", "Stop the current owner; never start concurrent demo or restore."],
            ["GRID:MODE? timeout", "Do not write grid mode because its baseline cannot be restored."],
            ["FILTER cutoff mismatch", "Treat as failure; preserve cutoffs unless explicitly requested."],
            ["AUTO became 28K", "Restore memory depth from the guarded baseline."],
            ["No cursor delta in log", "No such SCPI query is documented; read the panel value."],
            ["Truncated binary response", "Use PyVISA or built-in raw-vxi11 exact-length completion."],
            ["Point count mismatch", "Use returned_points and actual payload length."],
            ["measure returned *", "Check signal, scale, and trigger; the value is unavailable."],
            ["Trigger-level mismatch", "Record readback and retry once at most."],
            ["Memory-depth mismatch", "Trust the readback, not the request."],
            ["Missing NPZ/PNG", "Install optional extras with pip install -e \".[plot]\"."],
            ["Unexpected volts", "Use --raw-only and compare with a calibrated source."],
        ], [55 * mm, 111 * mm], st, font_size=7.7),
        p("Practical validation order", st["h2"]),
        bullet("Run status and record identity and firmware.", st),
        bullet("Run channel-get and deliberately enable the required channel.", st),
        bullet("Capture BYTE with --raw-only and inspect returned_points.", st),
        bullet("Compare VPP/VMAX/VMIN with a known test signal.", st),
        bullet("Only then use provisional volts and automated analysis.", st),
        callout("Do not diagnose with reset", "The project intentionally exposes no factory reset, clear, calibration, or LAN setters. Repair transport errors by closing and reconnecting the session, not by changing instrument configuration.", st, PALE_YELLOW), PageBreak(),
    ]

    story += [
        p("9. Evidence and live screen", st["h1"]),
        make_table([
            ["Area", "Result"],
            ["Identity", "UNI-T,MP720115; HW 1.07; SW 5.00.0.19"],
            ["Channels/timebase", "queries and representative same-value setters"],
            ["Acquisition/trigger", "type, memory, averages, edge settings, verified level"],
            ["Measurements", "VPP, VMAX, VMIN, VAMP, VRMS"],
            ["Waveform", "BYTE CH1, 697 returned samples, exact state restoration"],
            ["Screen", "1,152,054-byte 800x480 BMP through raw VXI-11 and PyVISA"],
            ["Verified screensaver", "25 PASS, 0 WARN, 0 FAIL, verified restoration"],
            ["Cursor smoke test", "10 PASS, 0 WARN, 0 FAIL, verified restoration"],
            ["Cinematic smoke test", "25 SENT, exit 0, all recovery groups verified"],
            ["Offline tests", "164 tests; no instrument connection"],
        ], [50 * mm, 116 * mm], st, font_size=7.8),
        Spacer(1, 3 * mm),
        Image(str(SCREENSHOT), width=166 * mm, height=99.6 * mm),
        p("800x480 screen captured from the live MP720115 with :DISPlay:DATA?.", st["caption"]), PageBreak(),
    ]

    story += [
        p("10. Sources and remaining work", st["h1"]),
        p("Primary source", st["h2"]),
        p("UNI-T, <b>UPO3000E &amp; UPO2000E Series Oscilloscope Programming Manual</b>, 94 pages:<br/><link href=\"https://unitrend.oss-cn-hongkong.aliyuncs.com/uploads/attach/20250624/171046f30ffda67ff849c78002191.pdf\" color=\"#1666A8\">official PDF</link>", st["body"]),
        p("Project documentation", st["h2"]),
        bullet("README.md - overview and quick start.", st),
        bullet("docs/COMMAND_REFERENCE.md - CLI options and SCPI mapping.", st),
        bullet("docs/VALIDATION.md - live, documented-only, and firmware evidence.", st),
        bullet("docs/ARCHITECTURE.md - layers, block completion, and restoration.", st),
        bullet("docs/TROUBLESHOOTING.md - failure diagnosis.", st),
        bullet("docs/OPERATOR_RUNBOOK.md - closed-loop operation and recovery.", st),
        p("Remaining uncertainties", st["h2"]),
        bullet("USB-B interface type requires physical enumeration.", st),
        bullet("Absolute voltage accuracy requires calibrated DC and sine sources.", st),
        bullet("Trigger-level behavior across V/div and offset needs bounded characterization.", st),
        bullet("RUN/STOP/SINGLE needs an independent status or event counter.", st),
        bullet("WORD/ASCII and CH2-CH4 waveform capture are documented but not live-verified.", st),
        bullet("Sustained waveform refresh rate needs a targeted benchmark.", st),
        callout("Recommendation", "For automated laboratory work, use Ethernet/VXI-11 with pyvisa-py, one long-lived session, and the allowlisted MP720115 controller. This is the simplest confirmed path without Windows or NI-VISA.", st, PALE_GREEN),
    ]
    return story


def main() -> int:
    register_fonts()
    st = styles()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4,
        rightMargin=22 * mm, leftMargin=22 * mm,
        topMargin=19 * mm, bottomMargin=21 * mm,
        title="mp72-control: MP720115 control over Ethernet/VXI-11",
        author="mp72-control project",
        subject="SCPI, PyVISA, waveform, trigger, and acquisition control",
    )
    doc.build(build_story(st), onFirstPage=footer, onLaterPages=footer)
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
