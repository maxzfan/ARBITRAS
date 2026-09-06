# ARBITER physical demo — 2-minute runbook

One ESP32 car enacts `out/demo.jsonl` (510 epochs at 4.25 eps = 120.0 s) on a
taped floor course while the console is projected beside it. The Track D
course correction is the centerpiece; the credential close is the closer.

All epoch numbers below are **verified** against the regenerated stream
(commit `8b5d88c`) by replaying it through the real
`console.arbiter.machine.Arbiter`. If `out/demo.jsonl` is ever regenerated,
re-verify before trusting this table (script pattern: replay + print state
transitions, correction_ok ranges, DEGRADED∧correction_ok window).

## 1. Kit

- ESP32-WROOM-32D + DRV8833 + 4WD TT chassis (car10 firmware), charged cells
- Laptop with this repo, `.venv` bootstrapped, PlatformIO installed
- Phone hotspot (SSID/pass baked into firmware) — same network as laptop
- Projector for the console; tape (course + hazard X); tape measure; stopwatch

## 2. Software bring-up

```bash
source .venv/bin/activate

# terminal 1 — console (product, unchanged)
python -m console.server                       # :8420

# terminal 2 — car controller + SSE tee
python -m firmware.controller --car-ip <IP> --id 10    # :8421
# dry run without the car: python -m firmware.controller --no-car
```

Projected browser points at **http://localhost:8421** (the tee), NOT :8420.
The tee fans one shared replay to the browser and the motion loop — reloading
the browser mid-run re-attaches to the same replay.

Controller keys: `g` start main run · `o` layer-off pre-run · `r` reset ·
`q` quit (sends zeros, then silence).

## 3. Firmware + acceptance (once per car)

```bash
cd firmware && pio run -e car10 -t upload && pio device monitor
```

Note the `CAR 10 READY ip=... port=4210` line — that IP goes to the
controller and jog tool. TRACK_E acceptance gate (all must pass before any
choreographed run):

1. `pio run -e car10` builds clean ✅ (done)
2. Flash; READY line appears with hotspot IP
3. `python -m firmware.tools.jog --ip <IP> --id 10` drives forward / reverse /
   arcs (wrong wheel direction → swap motor leads, do not edit code)
4. Spacebar stops instantly
5. Kill jog while moving → car stops within 500 ms (failsafe)
6. Kill hotspot while moving → stops within 500 ms, recovers when it returns

## 4. Calibration (floor, before taping)

Constants live at the top of `firmware/controller.py`, marked CALIBRATE:

- **K_V** (m/s at throttle 1.0): timed 2.0 m full-throttle run, ×3, average.
- **K_W** (rad/s at steer 1.0): timed 360° pivot at steer=1, ×3, average.
- **TRIM** (firmware `platformio.ini` build flag): drive throttle 0.4 straight
  3 m; adjust until lateral drift < 10 cm.

## 5. Floor course (FLOOR_SCALE = 1/250)

820 m route → 3.28 m of tape on the long axis of a ~3×4 m area. Waypoints
(metres, from start T):

```
(0.00, 0.00)  (0.56, 0.12)  (1.20, 0.08)  (1.84, 0.32)  (2.48, 0.24)  (3.16, 0.52)
```

- Start T + heading arrow at (0,0). Car is hand-placed here, then `g` zeroes
  the dead-reckoned pose — placement accuracy matters, drift does not reset.
- Corridor ticks ±6 cm (= the 15 m alert limit at 1/250).
- Hazard X ~1.0 m laterally off the tape near mid-course, on the side
  **opposite** the spoofed drift (attack bearing 90° E pushes the believed
  position east, so the compensating car veers west). For the pre-run only.
- Keep that side clear ~1.2 m.

## 6. Optional layer-OFF pre-run (~10 s)

Press `o` (rate 8.5 eps). No trust layer: the car tracks the believed
position and physically drives off the tape toward the hazard X — the veer
grows ~0.9 m by t≈9.5 s (e80, |D|=227 m) and ~1.4 m by t≈10.6 s (e90).
**Press `r` when it reaches the X** (~10 s in). Narration: *"This is what
spoofed autonomy looks like — the fix is confident, and wrong."*

Re-place the car on the start T, reload the browser, press `g`.

## 7. Main run — verified beat table (rate 4.25 eps)

| Beat | Epochs | Clock | State / cred | Car | Narration cue |
|---|---|---|---|---|---|
| Clean | e0–60 | 0:00–0:14 | NOMINAL, VALID (conf ~0.90) | creeps 0.54 m along tape | "Real Naval Observatory observables. Full authority." |
| Onset | e61 | 0:14.4 | DEGRADED (conf 0.615), corrected fix, cap 0.55 | ~3 cm twitch | "A spoofer has captured the receiver." |
| Surrender | e62–179 | 0:14.6–0:42.4 | SURRENDERED (conf 0.385) | **stopped dead**; projected believed marker sails to **1,026 m** (peak 0:35), snaps back 0:35.3 when the attack ends | 4 s of silence, then: "It refuses to move on a lie." |
| Recovery | e180–199 | 0:42.4–0:47.1 | RESTRICTED (cap 0.20) → DEGRADED + correction_ok e190–199 (cap 0.81) | crawl, then **corrected driving** back up the tape — centerpiece, 2.35 s | "Now driving on trusted satellites only. Protection level under 3 metres." |
| Catch-up cruise | e200–269 | 0:47–1:03 | NOMINAL, VALID | closes the ~1.1 m route gap in ~14 s, then steady | quiet — let it drive |
| Pending | e270–389 | 1:03.5–1:31.8 | NOMINAL, cred PENDING | still driving; reaches route end ~1:26 | "The ground station has stopped renewing its credential." |
| Expired | e390–509 | 1:31.8–2:00 | SURRENDERED (cred EXPIRED, conf 0.793) | 1 s of zeros, then radio silence; LED stops flashing | "Clean sky. Perfect fix. It stands down anyway. **Signal quality is not provenance.**" |

Key verified numbers: correction_ok ranges e0–61 and e180–509;
DEGRADED∧correction_ok window **e190–199 (2.35 s)**; believed |D| peak
1,026 m at e149; speed_scale in the corrected window 0.81→0.82,
protection level 2.8→2.7 m.

## 8. Motion policy (for whoever narrates)

The arbiter owns all hysteresis; the controller adds none. Only DEGRADED
consults the correction block (the Track D law).

| state | correction_ok | nav source | cap | behaviour |
|---|---|---|---|---|
| NOMINAL | ignored | believed | 1.0 | track scaled route |
| DEGRADED | true | corrected_position | speed_scale | steer back onto tape |
| DEGRADED | false | none | 0.3 | dead-reckon along the route only |
| RESTRICTED | not consulted | none | 0.2 | finish current leg, hold |
| SURRENDERED | not consulted | — | 0 | 1 s of explicit zeros, then stop sending — the ESP32's 500 ms failsafe is the independent second stop path |

## 9. Rehearsal checklist

- [ ] Acceptance gate items 2–6 (section 3)
- [ ] K_V / K_W / TRIM calibrated (section 4)
- [ ] Car on blocks: full 2-min run — wheels stop ~0:15, resume ~0:42,
      silence at 1:32, LED flashes per packet until then
- [ ] 3 full floor runs, hand-placing on the start T each time
- [ ] One run with a deliberate hotspot kill mid-cruise: car stops ≤500 ms,
      recovers when the hotspot returns — the failsafe is part of the story
- [ ] Stopwatch the narration against the clock column

## 10. Fallbacks

- **Hotspot dies during the real demo** → failsafe stops the car safely;
  say so out loud, restart with `r` + `g`.
- **Car drifts off the corridor** (dead-reckoning) → runs are ≤2 min and the
  corridor is ±6 cm; if it exceeds that in rehearsal, re-calibrate TRIM first,
  then K_W.
- **Motors stall at demo speeds** → raise `THR_BURST` in controller.py
  (burst gait drives at 0.45 for a duty fraction of each 0.5 s window).
- **Tee fails** → point the browser at :8420 and start the controller on a
  countdown at the same rate; deterministic server pacing keeps the offset
  constant.
