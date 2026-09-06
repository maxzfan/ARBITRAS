# TRACK E / FIRMWARE — ESP32 car firmware scope

This file is the complete specification for `firmware/`. It is written
to be handed to Claude Code and executed. Everything interface-shaped in here
is FROZEN — the controller on the laptop is built against it. Do not redesign
the packet format, the port, the pin map, or the failsafe behaviour. If
something in here is impossible, stop and say so instead of substituting.

## Role boundary

The car is a dumb actuator. The firmware receives (throttle, steer) over UDP
and drives two motors. It contains NO localization, NO waypoints, NO state
machine, NO knowledge of GNSS, spoofing, confidence, or credentials. All of
that lives on the laptop. If a feature seems missing, it is missing on purpose.

## Hardware facts (do not change, do not "improve")

- Board: ESP32-WROOM-32D dev module ("esp32dev" in PlatformIO).
- Driver: Adafruit DRV8833, two-input-per-channel scheme. Per channel, one
  input gets PWM and the other is held LOW; swapping which pin gets the PWM
  reverses the motor. Both LOW = coast. Never drive both HIGH (brake) in this
  firmware.
- Pin map:
    GPIO 25 -> AIN1   (left motor)
    GPIO 26 -> AIN2   (left motor)
    GPIO 32 -> BIN1   (right motor)
    GPIO 33 -> BIN2   (right motor)
    Onboard LED = GPIO 2
- PWM: LEDC, 1 kHz, 8-bit resolution, one channel per pin (4 channels).
- Motors are TT gear motors. Below ~30% duty they stall rather than creep.
- All four motor pins must be driven LOW in setup() before anything else
  happens, and must be LOW whenever the firmware is not commanding motion.

## Project layout (PlatformIO)

    firmware/
      platformio.ini
      src/main.cpp

platformio.ini:

    [env]
    platform = espressif32
    board = esp32dev
    framework = arduino
    monitor_speed = 115200

    [env:car10]
    build_flags = -DCAR_ID=10

    [env:car11]
    build_flags = -DCAR_ID=11

One source file. CAR_ID comes only from the build flag. The two binaries must
differ in nothing else.

## Tunable constants — one block at the top of main.cpp, nowhere else

    WIFI_SSID, WIFI_PASS      // hotspot credentials, filled by hand
    UDP_PORT        = 4210
    FAILSAFE_MS     = 500
    MIN_DUTY        = 77      // ~30% of 255; dead-zone clamp, tuned on the floor
    MAX_DUTY        = 255
    TRIM            = 0.0     // [-0.3, 0.3], per-car steer bias, tuned on the floor

MIN_DUTY and TRIM are placeholders to be tuned by hand with the car on the
floor. Do not tune them in code. Do not add other magic numbers.

## Network protocol — FROZEN

Transport: UDP, port 4210, car joins the hotspot as a station.

Packet: plain ASCII, newline-terminated, comma-separated. NOT JSON — no
ArduinoJson dependency.

    <car_id>,<throttle>,<steer>\n
    e.g.  10,0.50,-0.20\n

- car_id: integer. Packets whose car_id != CAR_ID are silently ignored.
  (Both cars receive the same broadcast; this is the filter.)
- throttle in [-1, 1], forward positive.
- steer in [-1, 1], right positive.
- Malformed packets are ignored and do NOT reset the failsafe timer.
- Any valid packet (including 0,0) resets the failsafe timer.

On boot, after WiFi connects, print to serial: CAR_ID, the assigned IP, and
the port, one line, greppable:

    CAR 10 READY ip=172.20.10.4 port=4210

## Control law

    steer_eff = clamp(steer + TRIM, -1, 1)
    left  = clamp(throttle + steer_eff, -1, 1)
    right = clamp(throttle - steer_eff, -1, 1)

Per side, map to the DRV8833:
- magnitude 0: both pins LOW.
- magnitude > 0: duty = MIN_DUTY + |v| * (MAX_DUTY - MIN_DUTY), so any nonzero
  command clears the stall zone. Sign picks the pin: positive -> PWM on IN1,
  IN2 LOW; negative -> PWM on IN2, IN1 LOW.
- A commanded zero must always produce both pins LOW — the dead-zone offset
  must never turn 0 into motion.

## Failsafe — the most important requirement in this file

If no valid packet has arrived for FAILSAFE_MS milliseconds, drive all four
motor pins LOW and keep them LOW until a valid packet arrives. This must hold
through every code path: WiFi drop, controller crash, laptop sleep, hotspot
death. This is what makes the demo safe and what makes SURRENDERED fail
closed. Check it in loop() on every iteration using millis(); do not rely on
WiFi status callbacks.

Additionally: if WiFi disconnects, motors LOW immediately, then attempt
reconnection forever (WiFi.setAutoReconnect plus an explicit reconnect loop);
UDP listener re-armed after reconnect.

## Liveness indicator

Toggle the onboard LED on every valid packet received. Solid off + car not
moving = failsafe or no packets; flickering = healthy stream. This must be
visible behaviour, not a serial print.

## loop() structure (target, not law)

    read all pending UDP packets (drain the socket, keep the last valid one)
    if valid packet: parse, filter by CAR_ID, update setpoints, stamp millis(), toggle LED
    if millis() - last_valid > FAILSAFE_MS: setpoints = 0 and pins LOW
    apply control law to setpoints

Keep loop() under a few ms; no delay() longer than 1 ms anywhere.

## Serial debugging

115200 baud. Print the READY line on boot. Optionally print parsed
(throttle, steer) at most 5 times/sec when a DEBUG flag is set; default off.
Nothing else on serial — it becomes unreadable during tuning otherwise.

## Companion tool (same task, laptop side)

`firmware/tools/jog.py` — Python 3, stdlib only (socket, termios or curses):

    python -m firmware.tools.jog --ip 172.20.10.4 --id 10

Arrow keys set throttle/steer in 0.1 steps, spacebar zeroes both, q quits and
sends a final zero. Sends the frozen packet format at 20 Hz continuously
(so releasing the keys and quitting both stop the car via zero + failsafe).
Prints what it sends on one status line.

## Acceptance — all six, in order, before the firmware is "done"

1. `pio run -e car10` and `-e car11` both build clean.
2. Flashed board prints the READY line; IP noted.
3. jog drives the car: forward, reverse, arcs both directions. Wheels spinning
   the wrong way is a wiring fix (swap that motor's leads), not a code change.
4. Spacebar in jog stops the car instantly.
5. Kill jog with the car moving: car stops within ~500 ms (failsafe).
6. Turn off the hotspot with the car moving: car stops within ~500 ms, and
   recovers (LED flickers again) when the hotspot returns and jog restarts.

Do not proceed to the controller until 5 and 6 pass on BOTH cars.

## Explicitly out of scope

- ArduinoJson or any external library beyond the ESP32 Arduino core.
- OTA updates, web servers, mDNS, Bluetooth, telemetry back-channels.
- Any smoothing, ramping, or PID on the car. The laptop owns dynamics.
- Persisting state across reboots.
- Changing the packet format, port, pins, or failsafe timing.