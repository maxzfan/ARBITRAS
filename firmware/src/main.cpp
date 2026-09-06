// ARBITRAS demo car firmware. Spec: tracks/TRACK_G.md (FROZEN).
// The car is a dumb actuator: (throttle, steer) over UDP -> two DRV8833
// channels. No localization, no state machine, no GNSS knowledge.

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#ifndef CAR_ID
#error "CAR_ID must come from the build flag (see platformio.ini)"
#endif

// ---- Tunable constants (only block of magic numbers; see TRACK_G.md) ----
static const char *WIFI_SSID = "FILL_ME_IN";   // hotspot credentials,
static const char *WIFI_PASS = "FILL_ME_IN";   // filled by hand
static const uint16_t UDP_PORT     = 4210;
static const uint32_t FAILSAFE_MS  = 500;
static const int      MIN_DUTY     = 77;    // ~30% of 255; dead-zone clamp, tuned on the floor
static const int      MAX_DUTY     = 255;
static const float    TRIM         = 0.0f;  // [-0.3, 0.3], per-car steer bias, tuned on the floor
// ------------------------------------------------------------------------

// Pin map (FROZEN)
static const int PIN_AIN1 = 25;  // left motor
static const int PIN_AIN2 = 26;  // left motor
static const int PIN_BIN1 = 32;  // right motor
static const int PIN_BIN2 = 33;  // right motor
static const int PIN_LED  = 2;   // onboard LED, liveness

// LEDC: 1 kHz, 8-bit, one channel per pin
static const int LEDC_FREQ = 1000;
static const int LEDC_RES  = 8;
static const int CH_AIN1 = 0, CH_AIN2 = 1, CH_BIN1 = 2, CH_BIN2 = 3;

// #define DEBUG_PRINT 1  // parsed (throttle, steer) at <=5 Hz when set

static WiFiUDP udp;
static float setThrottle = 0.0f;
static float setSteer    = 0.0f;
static uint32_t lastValidMs = 0;
static bool haveValid = false;   // no valid packet yet -> stay in failsafe
static bool ledState = false;

static float clamp1(float v) {
  if (v > 1.0f) return 1.0f;
  if (v < -1.0f) return -1.0f;
  return v;
}

static void allPinsLow() {
  ledcWrite(CH_AIN1, 0);
  ledcWrite(CH_AIN2, 0);
  ledcWrite(CH_BIN1, 0);
  ledcWrite(CH_BIN2, 0);
}

// Per side: magnitude 0 -> both LOW; else PWM on the sign-selected pin,
// duty = MIN_DUTY + |v| * (MAX_DUTY - MIN_DUTY). Never both HIGH.
static void driveChannel(int chFwd, int chRev, float v) {
  if (v == 0.0f) {
    ledcWrite(chFwd, 0);
    ledcWrite(chRev, 0);
    return;
  }
  float mag = v > 0 ? v : -v;
  int duty = MIN_DUTY + (int)(mag * (float)(MAX_DUTY - MIN_DUTY));
  if (duty > MAX_DUTY) duty = MAX_DUTY;
  if (v > 0) {
    ledcWrite(chRev, 0);
    ledcWrite(chFwd, duty);
  } else {
    ledcWrite(chFwd, 0);
    ledcWrite(chRev, duty);
  }
}

static void applyControl(float throttle, float steer) {
  float steerEff = clamp1(steer + TRIM);
  float left  = clamp1(throttle + steerEff);
  float right = clamp1(throttle - steerEff);
  driveChannel(CH_AIN1, CH_AIN2, left);
  driveChannel(CH_BIN1, CH_BIN2, right);
}

// Parse "<car_id>,<throttle>,<steer>\n". Returns true only for a valid
// packet addressed to CAR_ID. Malformed packets return false and must not
// reset the failsafe timer. Packets for another car are valid-but-ignored:
// they also must not feed our failsafe, so they return false here too.
static bool parsePacket(char *buf, int len, float *thr, float *steer) {
  buf[len] = '\0';
  char *p1 = strchr(buf, ',');
  if (!p1) return false;
  char *p2 = strchr(p1 + 1, ',');
  if (!p2) return false;
  *p1 = '\0';
  *p2 = '\0';
  char *end = nullptr;
  long id = strtol(buf, &end, 10);
  if (end == buf || *end != '\0') return false;
  float t = strtof(p1 + 1, &end);
  if (end == p1 + 1) return false;
  while (*end == ' ') end++;
  if (*end != '\0') return false;
  char *sstr = p2 + 1;
  // strip trailing newline/CR
  int slen = strlen(sstr);
  while (slen > 0 && (sstr[slen - 1] == '\n' || sstr[slen - 1] == '\r')) {
    sstr[--slen] = '\0';
  }
  float s = strtof(sstr, &end);
  if (end == sstr) return false;
  while (*end == ' ') end++;
  if (*end != '\0') return false;
  if (t < -1.0f || t > 1.0f || s < -1.0f || s > 1.0f) return false;
  if (id != CAR_ID) return false;  // silently ignore other cars
  *thr = t;
  *steer = s;
  return true;
}

static void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(1);
  }
}

void setup() {
  // Motor pins LOW before anything else happens.
  pinMode(PIN_AIN1, OUTPUT);
  pinMode(PIN_AIN2, OUTPUT);
  pinMode(PIN_BIN1, OUTPUT);
  pinMode(PIN_BIN2, OUTPUT);
  digitalWrite(PIN_AIN1, LOW);
  digitalWrite(PIN_AIN2, LOW);
  digitalWrite(PIN_BIN1, LOW);
  digitalWrite(PIN_BIN2, LOW);

  ledcSetup(CH_AIN1, LEDC_FREQ, LEDC_RES);
  ledcSetup(CH_AIN2, LEDC_FREQ, LEDC_RES);
  ledcSetup(CH_BIN1, LEDC_FREQ, LEDC_RES);
  ledcSetup(CH_BIN2, LEDC_FREQ, LEDC_RES);
  ledcAttachPin(PIN_AIN1, CH_AIN1);
  ledcAttachPin(PIN_AIN2, CH_AIN2);
  ledcAttachPin(PIN_BIN1, CH_BIN1);
  ledcAttachPin(PIN_BIN2, CH_BIN2);
  allPinsLow();

  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);

  Serial.begin(115200);
  connectWiFi();
  udp.begin(UDP_PORT);

  Serial.printf("CAR %d READY ip=%s port=%u\n", CAR_ID,
                WiFi.localIP().toString().c_str(), UDP_PORT);
}

void loop() {
  // WiFi drop: motors LOW immediately, reconnect forever, re-arm UDP.
  if (WiFi.status() != WL_CONNECTED) {
    allPinsLow();
    setThrottle = 0.0f;
    setSteer = 0.0f;
    haveValid = false;
    udp.stop();
    WiFi.reconnect();
    while (WiFi.status() != WL_CONNECTED) {
      delay(1);
    }
    udp.begin(UDP_PORT);
  }

  // Drain the socket, keep the last valid packet.
  bool gotValid = false;
  float thr = 0.0f, steer = 0.0f;
  int pktLen;
  while ((pktLen = udp.parsePacket()) > 0) {
    char buf[64];
    int n = udp.read(buf, sizeof(buf) - 1);
    if (n <= 0) continue;
    float t, s;
    if (parsePacket(buf, n, &t, &s)) {
      thr = t;
      steer = s;
      gotValid = true;
    }
  }

  if (gotValid) {
    setThrottle = thr;
    setSteer = steer;
    lastValidMs = millis();
    haveValid = true;
    ledState = !ledState;  // liveness: toggle per valid packet
    digitalWrite(PIN_LED, ledState ? HIGH : LOW);
#ifdef DEBUG_PRINT
    static uint32_t lastDbg = 0;
    if (millis() - lastDbg >= 200) {
      lastDbg = millis();
      Serial.printf("thr=%.2f steer=%.2f\n", setThrottle, setSteer);
    }
#endif
  }

  // Failsafe: checked every iteration via millis(), no WiFi callbacks.
  if (!haveValid || millis() - lastValidMs > FAILSAFE_MS) {
    setThrottle = 0.0f;
    setSteer = 0.0f;
    allPinsLow();
    return;
  }

  applyControl(setThrottle, setSteer);
}
