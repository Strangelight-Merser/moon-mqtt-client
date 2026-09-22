#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ArduinoMqttClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <esp_system.h>
#include <esp_wifi.h>
#include <esp_eap_client.h>
#include <sys/time.h>
#include <driver/gpio.h>
#include "b2_output.h"

// Credentials arrive over local USB provisioning, never through source code.
Preferences prefs;
WiFiClientSecure transport;
MqttClient mqtt(transport);
String settingsJson, serialLine, bootId;
String authMode, ssid, identity, username, password, broker, brokerUser, brokerPassword, brokerCA;
String prefix = "moon/esp32/s3";
uint16_t brokerPort = 8884;
bool configured = false, networkStarted = false, stateOn = false;
uint32_t sequence = 0, appliedCount = 0, lastConnectAttempt = 0, lastTelemetry = 0;
uint32_t wifiDeadline = 0;
bool wifiPaused = false, wifiWasConnected = false;
uint32_t nextWifiAttempt=0, wifiBackoffMs=5000, wifiAttempt=0;
volatile int pendingDisconnectReason = -1;
bool serialOverflow = false;
struct RecentCommand { String id; bool target; int64_t expires; };
RecentCommand recent[64];
size_t recentCount = 0, recentNext = 0;
struct IncomingMessage { String topic, body; bool retained; };
IncomingMessage incoming[8];
size_t incomingHead=0, incomingCount=0;

// endMessage()/subscribe() also poll the socket. Capture messages there too;
// never publish recursively from the receive callback.
void receiveMessage(int size) {
  if(size<0 || size>2048 || incomingCount>=8) {
    incomingCount=0;mqtt.stop();event("mqtt_receive_limit");return;
  }
  IncomingMessage message;
  message.topic=mqtt.messageTopic();message.retained=mqtt.messageRetain()==1;
  message.body.reserve(size);uint32_t start=millis();
  while(message.body.length()<(size_t)size && millis()-start<1000) {
    while(mqtt.available() && message.body.length()<(size_t)size)message.body+=char(mqtt.read());
    delay(1);
  }
  if(message.body.length()!=(size_t)size) {
    incomingCount=0;mqtt.stop();event("truncated_mqtt_request");return;
  }
  incoming[(incomingHead+incomingCount)%8]=message;++incomingCount;
}

void event(const char *name) {
  JsonDocument d; d["event"] = name; serializeJson(d, Serial); Serial.println();
}
int64_t nowMs() {
  timeval tv; gettimeofday(&tv, nullptr);
  return int64_t(tv.tv_sec) * 1000 + tv.tv_usec / 1000;
}
bool clockReady() { return nowMs() >= 1700000000000LL; }
String topic(const char *suffix) { return prefix + suffix; }
bool validString(JsonVariantConst v, size_t limit, bool allowEmpty = false) {
  if (!v.is<const char *>()) return false;
  JsonString s = v.as<JsonString>();
  return (allowEmpty || s.size() > 0) && s.size() <= limit && strlen(s.c_str()) == s.size();
}
bool loadSettings(const String &text) {
  JsonDocument d;
  if (deserializeJson(d, text) || !d.is<JsonObject>()) return false;
  for (const char *key : {"ssid", "password", "broker_host", "broker_user", "broker_password", "broker_ca"}) {
    if (!validString(d[key], strcmp(key,"broker_ca") == 0 ? 4096 : 256)) return false;
  }
  String mode=d["auth_mode"] | "";
  if (mode!="peap" && mode!="psk") return false;
  if (mode=="peap" && !validString(d["username"],256)) return false;
  if (d["ssid"].as<JsonString>().size() > 32 || !d["broker_port"].is<uint16_t>()) return false;
  if (d["broker_port"].as<uint16_t>() == 0 || !validString(d["prefix"], 128)) return false;
  String p = d["prefix"].as<String>();
  if (p.indexOf('+') >= 0 || p.indexOf('#') >= 0) return false;
  authMode=mode;
  ssid=d["ssid"].as<String>(); username=d["username"] | ""; password=d["password"].as<String>();
  identity=username;
  broker=d["broker_host"].as<String>(); brokerPort=d["broker_port"].as<uint16_t>();
  brokerUser=d["broker_user"].as<String>(); brokerPassword=d["broker_password"].as<String>();
  brokerCA=d["broker_ca"].as<String>(); prefix=p;
  return true;
}
bool publishText(const String &t, const String &payload, bool retain=true) {
  if (!mqtt.connected() || !mqtt.beginMessage(t, (unsigned long)payload.length(), retain, 1)) return false;
  if (mqtt.print(payload) != payload.length()) return false;
  return mqtt.endMessage() == 1;
}
void feedback(const String &correlation) {
  JsonDocument d;
  d["boot_id"]=bootId; d["sequence"]=++sequence;
  d["state"]=stateOn ? "ON" : "OFF"; d["correlation_id"]=correlation;
  String body; serializeJson(d,body);
  if (!publishText(topic("/device/feedback"),body)) event("feedback_publish_failed");
}
void telemetry() {
  JsonDocument d;
  d["boot_id"]=bootId; d["uptime_ms"]=millis(); d["free_heap"]=ESP.getFreeHeap();
  d["free_psram"]=ESP.getFreePsram(); d["rssi_dbm"]=WiFi.RSSI(); d["clock_ready"]=clockReady();
  d["state"]=stateOn?"ON":"OFF"; d["applied_count"]=appliedCount;
  d["output_kind"]=B2_OUTPUT_PIN < 0 ? "logical_state_no_gpio" : "configured_low_voltage_gpio";
  d["output_pin"]=B2_OUTPUT_PIN; d["apply_pulse_pin"]=B2_APPLY_PULSE_PIN;
  String body;serializeJson(d,body);publishText(topic("/device/telemetry"),body);
}
void handleMessage(const String &t, const String &body, bool retained) {
  if (retained) { event("retained_request_rejected"); return; }
  JsonDocument d;
  if (deserializeJson(d,body) || !d.is<JsonObject>() || !validString(d["id"],128)) { event("invalid_request"); return; }
  String id=d["id"].as<String>();
  if (t==topic("/device/query")) { feedback(id); return; }
  if (t!=topic("/device/set")) return;
  if (!clockReady() || !validString(d["target"],3) || !d["expires_at_ms"].is<int64_t>()) { event("invalid_command"); return; }
  String target=d["target"].as<String>();
  int64_t expiry=d["expires_at_ms"].as<int64_t>(), now=nowMs();
  if ((target!="ON" && target!="OFF") || expiry<=now || expiry-now>30000) { event("expired_or_invalid_command"); return; }
  bool nextState=target=="ON";
  for (size_t i=0;i<recentCount;++i) {
    if (recent[i].id==id) {
      if (recent[i].target!=nextState || recent[i].expires!=expiry) { event("conflicting_command_id"); return; }
      event("duplicate_command"); feedback(id); return;
    }
  }
  b2Apply(nextState);
  stateOn=nextState; ++appliedCount;
  recent[recentNext]={id,nextState,expiry}; recentNext=(recentNext+1)%64;
  if (recentCount<64) ++recentCount;
  JsonDocument applied;applied["event"]="applied";applied["id"]=id;
  applied["state"]=stateOn?"ON":"OFF";applied["applied_count"]=appliedCount;
  serializeJson(applied,Serial);Serial.println();
  feedback(id);
}
void configureClock(JsonVariantConst value) {
  if (!value.is<int64_t>()) return;
  int64_t ms=value.as<int64_t>();
  if (ms<1700000000000LL || ms>4102444800000LL) return;
  timeval tv={time_t(ms/1000),suseconds_t((ms%1000)*1000)};
  settimeofday(&tv,nullptr);event("clock_set_from_local_host");
}
void serialCommand(const String &line) {
  JsonDocument d;
  if (deserializeJson(d,line)) { event("invalid_serial_json"); return; }
  String op=d["op"]|"";
  if (op=="configure") {
    String candidate;serializeJson(d["config"],candidate);
    if (!loadSettings(candidate)) { event("invalid_config"); return; }
    mqtt.stop();WiFi.disconnect();networkStarted=false;wifiPaused=false;wifiWasConnected=false;nextWifiAttempt=0;
    if (prefs.putString("config",candidate)!=candidate.length()) { configured=false;event("config_save_failed");return; }
    settingsJson=candidate;configured=true;configureClock(d["epoch_ms"]);event("configured");
  } else if (op=="time") { configureClock(d["epoch_ms"]);
  } else if (op=="status") {
    JsonDocument out;out["event"]="status";out["configured"]=configured;
    out["wifi_connected"]=WiFi.status()==WL_CONNECTED;out["mqtt_connected"]=bool(mqtt.connected());
    out["clock_ready"]=clockReady();out["boot_id"]=bootId;out["applied_count"]=appliedCount;
    if (WiFi.status()==WL_CONNECTED) out["ip"]=WiFi.localIP().toString();
    serializeJson(out,Serial);Serial.println();
  } else if (op=="scan") {
    if(WiFi.status()==WL_CONNECTED) {event("scan_requires_disconnected_wifi");return;}
    wifiPaused=true;networkStarted=false;WiFi.disconnect();delay(100);
    wifi_country_t country;esp_wifi_get_country(&country);
    JsonDocument info;info["event"]="scan_country";info["country"]=String(country.cc).substring(0,2);
    info["first_channel"]=country.schan;info["channel_count"]=country.nchan;serializeJson(info,Serial);Serial.println();
    for(bool passive : {false,true}) {
      int n=WiFi.scanNetworks(false,true,passive,500);
      JsonDocument summary;summary["event"]="scan_result";summary["passive"]=passive;summary["count"]=n;
      serializeJson(summary,Serial);Serial.println();
      for(int i=0;i<n;++i) {
        JsonDocument row;row["event"]="scan_entry";row["ssid"]=WiFi.SSID(i);row["channel"]=WiFi.channel(i);
        row["rssi_dbm"]=WiFi.RSSI(i);row["auth_mode"]=int(WiFi.encryptionType(i));serializeJson(row,Serial);Serial.println();
      }
      WiFi.scanDelete();
    }
    event("scan_done");
  } else if (op=="restart") { event("restarting");Serial.flush();ESP.restart();
  } else if (op=="retry_wifi") { wifiPaused=false;networkStarted=false;nextWifiAttempt=millis()+5000;WiFi.disconnect();event("wifi_retry_requested");
  } else { event("unknown_serial_operation"); }
}
void setup() {
  Serial.begin(115200);Serial.setTimeout(500);delay(200);
  if (!b2Setup()) { event("invalid_b2_wiring_configuration"); while (true) delay(1000); }
  prefs.begin("moon-campus",false);settingsJson=prefs.getString("config","");
  configured=loadSettings(settingsJson);
  WiFi.persistent(false);WiFi.mode(WIFI_STA);WiFi.setAutoReconnect(false);
  // USB-powered device: avoid modem-sleep latency during interactive control.
  if(!WiFi.setSleep(false))event("wifi_sleep_disable_failed");
  if(esp_wifi_set_country_code("CN",true)!=ESP_OK)event("country_config_failed");
  WiFi.onEvent([](WiFiEvent_t e,WiFiEventInfo_t info) {
    if(e==ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      pendingDisconnectReason=info.wifi_sta_disconnected.reason;
    }
  });
  mqtt.onMessage(receiveMessage);
  mqtt.setConnectionTimeout(5000);mqtt.setKeepAliveInterval(15000);mqtt.setCleanSession(true);
  event("moon_esp32_boot");
}
void loop() {
  if(pendingDisconnectReason>=0) {
    int reason=pendingDisconnectReason;pendingDisconnectReason=-1;
    JsonDocument d;d["event"]="wifi_disconnected";d["reason"]=reason;
    serializeJson(d,Serial);Serial.println();
  }
  while(Serial.available()) {
    char c=Serial.read();
    if(c=='\n') { if(!serialOverflow && serialLine.length())serialCommand(serialLine);serialLine="";serialOverflow=false; }
    else if(c!='\r') {
      if(serialOverflow)continue;
      if(serialLine.length()<8192)serialLine+=c;
      else { serialLine="";serialOverflow=true;event("serial_request_too_large"); }
    }
  }
  if(!configured) { delay(10);return; }
  if(wifiWasConnected && WiFi.status()!=WL_CONNECTED) {
    wifiWasConnected=false;networkStarted=false;
    transport.stop();incomingCount=0;incomingHead=0;
    nextWifiAttempt=millis()+wifiBackoffMs;
    event("wifi_link_lost_reconnect_scheduled");
  }
  if(WiFi.status()!=WL_CONNECTED && !networkStarted && !wifiPaused && int32_t(millis()-nextWifiAttempt)>=0) {
    // UJS-1X documents PEAP/GTC without a CA. This is scoped to this network;
    // MQTT uses the provisioned private CA and never disables verification.
    int count=WiFi.scanNetworks(false,true);int matches=0;
    for(int i=0;i<count;++i)if(WiFi.SSID(i)==ssid)++matches;
    WiFi.scanDelete();
    JsonDocument d;d["attempt"]=++wifiAttempt;d["event"]="wifi_scan";d["total_aps"]=count;d["target_matches"]=matches;serializeJson(d,Serial);Serial.println();
    if(authMode=="peap") {
      WiFi.begin(ssid.c_str(),WPA2_AUTH_PEAP,identity.c_str(),username.c_str(),password.c_str());
      event("enterprise_wifi_connecting");
    } else {
      esp_wifi_sta_enterprise_disable();
      WiFi.begin(ssid.c_str(),password.c_str());
      event("hotspot_wifi_connecting");
    }
    networkStarted=true;wifiDeadline=millis()+45000;
  }
  if(WiFi.status()!=WL_CONNECTED) {
    if(networkStarted && !wifiPaused && int32_t(millis()-wifiDeadline)>=0) {
      WiFi.disconnect();networkStarted=false;
      nextWifiAttempt=millis()+wifiBackoffMs;
      JsonDocument retry;retry["event"]="wifi_timeout_retry_scheduled";retry["delay_ms"]=wifiBackoffMs;
      serializeJson(retry,Serial);Serial.println();
      wifiBackoffMs=wifiBackoffMs<30000?min(wifiBackoffMs*2,uint32_t(30000)):30000;
    }
    delay(10);return;
  }
  if(bootId.isEmpty()) {
    uint8_t random[16];esp_fill_random(random,sizeof(random));char hex[33];
    for(int i=0;i<16;++i)sprintf(hex+2*i,"%02x",random[i]);bootId=hex;
  }
  if(!wifiWasConnected) {
    wifiWasConnected=true;networkStarted=true;wifiBackoffMs=5000;
    JsonDocument d;d["event"]="wifi_connected";d["ip"]=WiFi.localIP().toString();
    d["rssi_dbm"]=WiFi.RSSI();d["boot_id"]=bootId;serializeJson(d,Serial);Serial.println();
    configTime(0,0,"ntp.aliyun.com","pool.ntp.org");
  }
  if(!clockReady()) { delay(10);return; }
  if(!mqtt.connected()) {
    if(millis()-lastConnectAttempt<5000) { delay(10);return; }
    lastConnectAttempt=millis();
    transport.setCACert(brokerCA.c_str());transport.setHandshakeTimeout(8);
    mqtt.setId("moon-esp32-"+WiFi.macAddress());mqtt.setUsernamePassword(brokerUser,brokerPassword);
    mqtt.beginWill(topic("/device/availability"),true,1);mqtt.print("offline");mqtt.endWill();
    if(!mqtt.connect(broker.c_str(),brokerPort)) {
      JsonDocument d;d["event"]="mqtt_connect_failed";d["code"]=mqtt.connectError();serializeJson(d,Serial);Serial.println();return;
    }
    if(!mqtt.subscribe(topic("/device/set"),1) || mqtt.subscribeQoS()==128 ||
       !mqtt.subscribe(topic("/device/query"),1) || mqtt.subscribeQoS()==128) {
      mqtt.stop();event("subscription_failed");return;
    }
    if(!publishText(topic("/device/availability"),"online")) {mqtt.stop();return;}
    event("mqtt_ready");telemetry();lastTelemetry=millis();
  }
  mqtt.poll();
  if(incomingCount>0) {
    IncomingMessage message=incoming[incomingHead];
    incoming[incomingHead]=IncomingMessage{};
    incomingHead=(incomingHead+1)%8;--incomingCount;
    handleMessage(message.topic,message.body,message.retained);
  }
  if(millis()-lastTelemetry>=5000) { telemetry();lastTelemetry=millis(); }
  delay(5);
}
