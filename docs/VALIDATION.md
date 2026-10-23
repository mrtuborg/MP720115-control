# Validation

Command syntax comes from the official [UPO3000E & UPO2000E Series Oscilloscope Programming Manual](reference/UNI-T_UPO3000E_UPO2000E_Programming_Manual.pdf), downloaded from [UNI-T's official storage](https://unitrend.oss-cn-hongkong.aliyuncs.com/uploads/attach/20250624/171046f30ffda67ff849c78002191.pdf). The tested instrument reports firmware `5.00.0.19`; the manual is authoritative for spelling, while the live results below are authoritative for observed behavior.

Live work used `UNI-T,MP720115,0000000000000,5.00.0.19`. Packaging and offline tests do not contact the instrument.

## 1. Live-verified behavior

| Area | Verified behavior |
|---|---|
| Identity | `*IDN?`, `:SYSTem:VERSion?` returned `HW:1.07;SW:5.00;PD:1.00;ICV:1.2.2;` |
| Channels | DISPlay, COUPling, SCALe, OFFSet, PROBe queries; same-value COUPling and SCALe writes |
| Timebase | MODE, SCALe, OFFSet queries; same-value SCALe write |
| Acquisition | TYPE, memory depth, average count queries; same-value TYPE write |
| Trigger | MODE, source, edge slope, level, sweep, coupling queries; same-value slope/sweep writes |
| Measurements | VPP, VMAX, VMIN, VAMP, VRMS on CH1 |
| Waveform | BYTE CH1 source/format/points, preamble, data through raw VXI-11 and PyVISA; exact transfer-state restoration |
| Screen | 1,152,054-byte valid 800x480 BMP through raw VXI-11 and PyVISA after block completion |
| Run/Stop | STOP then RUN commands; instrument remained responsive, but no state readback exists |
| Offset sign | CH1 offsets 0, 0.12, 0.24 V shifted code by approximately offset/yinc |

Artifacts are `docs/examples/live_waveform_ch1.csv` and `docs/examples/live_screen_test.png`.

## 2. Documented but not generally live-verified

- channel offset, bandwidth-limit, probe, and display setters;
- timebase mode and offset writes;
- acquisition mode write, whose readback fails;
- EDGE trigger mode/source/coupling writes;
- VTOP, VBASE, VMIDDLE, VAVERAGE, and OVERSHOOT measurements;
- WORD and ASCII waveform formats;
- CH2-CH4 waveform capture.

Memory depth, average count, and trigger level were exercised in one demonstration session. That is limited characterization, not complete verification: `7K` depth and one 0.8 V trigger level were rejected, and count was accepted only under AVERAGE. The library therefore verifies them by readback.

Voltage conversion `(raw - yref) * yinc - yorigin` remains **provisional**. Offset sign was checked, but absolute accuracy was not compared with a calibrated source.

Display/FUNCtion/cursor syntax and ranges come from the manual. Typed setters, aliases, fail-closed readback, recovery, and simulated screen behavior have offline coverage. The manual documents no numeric SCPI cursor-result query. Exact on-screen visibility, filter rounding, and XY behavior remain firmware-dependent unless listed below.

## 3. Firmware observations

1. `:ACQuire:MODE?` times out despite being documented.
2. VXI-11 END is set on every 1024-byte fragment; complete long blocks by declared length.
3. `:WAVeform:POINts` requests for 50, 120, and 500 each returned 697 BYTE samples. Use actual payload length.
4. Waveform point count drifts during RUN and is more stable under STOP.
5. `:WAVeform:DATA?` on a disabled channel times out and poisons the link; the library refuses it first.
6. FREQ, PER, RIS, and FALL return literal `*` when unavailable.
7. Readbacks use mixed case and abbreviations such as `CHANnel1`, `HRESolution`, and `1x`.

### Demonstration observations on one firmware 5.00.0.19 unit

8. Average count changed only while acquisition type was AVERAGE. The implementation temporarily selects AVERAGE, sets and reads the count, then returns to the requested type.
9. `:ACQuire:MEMory:DEPTh 7K` read back as AUTO twice. PEAKDETECT with AUTO read as 28K. Non-AUTO mismatch is failure; concrete readback for AUTO is WARN.
10. `:TRIGger:LEVel 0.8` read back as -11.84 V and then +8.16 V on retry. The cause was not established. Level is applied last and mismatch returns code 1.
11. RUN, STOP, and SINGLE states remain unproven. Noise effects from bandwidth limit, HRES, and averaging were inconclusive on a nonstationary floating probe.
12. Long `:DISPlay:DATA?` responses were corrupted twice after short-query bursts on one raw link. A separate fresh-link read immediately returned the correct 1,152,054 bytes. The screensaver therefore reconnects transport before each screen transfer.
13. `:DISPlay:GRID:MODE?` times out, while `:DISPlay:TYPE?` returns `YT` with two trailing NUL bytes. Grid mode is unreadable and never changed; NUL padding is normalized.
14. FILTER high=2000 Hz read back as 1500 Hz twice. FUNCtion modes also changed memory depth from AUTO to 28K. Default filter scenes preserve cutoffs, and memory depth is guarded.
15. Full screensaver smoke test `mp72-screensaver-smoke-20261023-06`: 25 PASS, 0 WARN, 0 FAIL across display/FFT/MATH/XY/FILTER, exit 0, and verified restoration including AUTO memory depth.
16. A concurrent restore could not open a second link and returned device error 3. Stop the owning process first; never run parallel restores.
17. `--retain-screens 0` performs full in-memory BMP decode, hash, pixel difference, and repeated readback without storing BMP files.
18. Cursor MODE?/SOURCE? time out under TYPE=CLOSE, while TYPE?/CURA?/CURB? work. Temporary TIME reveals MODE/SOURCE. Partial recovery is written before activation.
19. TRACK couples A/B unpredictably by type. Observed TIME A became 400 after requesting A=300/B=400; AMPLITUDE A became 29 after A=B=128. The demo never writes A/B in TRACK. A CH3/CH4 cursor smoke test completed 10 PASS, 0 WARN, 0 FAIL with verified restoration.
20. The fast cinematic show deliberately records 25 `SENT` frames without per-frame readback/BMP. Structural tests prove query-free `apply_scene`, no RUN/STOP, valid cursor ranges, and trigger sources limited to enabled channels.
21. Live smoke test `mp72-cinematic-live-smoke-20261023-01`: 25 SENT frames, exit 0, completed status, and overall restoration true. Cursor 5/5, visual/FUNCtion 14/14, and channel/timebase/EDGE-trigger 13/13 fields restored. Baseline CH1/CH2 ON and CH3/CH4 OFF returned exactly. With dwell 0.25, scenes took about 3-5 seconds because each has multiple sequential writes. This validates lifecycle and restoration, not the physical result of each SENT scene.

## 4. Offline tests

`python3 -m pytest -q` runs 164 tests without the instrument. Coverage includes block and preamble parsing, validation, long-block completion, disabled channels, state restoration, forbidden commands, configuration examples, display/FUNCtion/cursor methods, CH1-CH4 frame planning, partial recovery for closed cursors, TRACK coordinate protection, fast cinematic SENT-only planning, mismatch refusal, atomic recovery, exception/SIGINT/SIGTERM cleanup, restore mode, in-memory BMP validation, bounded BMP rotation, raw VXI-11 destroy/reconnect lifecycle, isolated baseline queries, memory-depth side-effect recovery, and forbidden-command source scanning.

No `*RST`, `*CLS`, calibration, storage deletion, LAN-setting, or auto-setup command was sent to the instrument.
