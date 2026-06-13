#ifndef WIFI_OTA_H
#define WIFI_OTA_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* WiFi configuration */
#define WIFI_MAX_SSID_LEN       32
#define WIFI_MAX_PASS_LEN       64
#define WIFI_CONNECT_TIMEOUT_MS 30000
#define WIFI_MAX_RETRIES        3

/* OTA configuration */
#define OTA_CONNECT_TIMEOUT_MS  30000
#define OTA_RECV_TIMEOUT_MS     10000
#define OTA_BUF_SIZE            1024

/* Default OTA server URL base */
#define OTA_URL_BASE            "https://update.neuroresonator.com/firmware/"

/* OTA status */
typedef enum {
    OTA_IDLE         = 0,
    OTA_CHECKING     = 1,
    OTA_AVAILABLE    = 2,
    OTA_DOWNLOADING  = 3,
    OTA_SUCCESS      = 4,
    OTA_FAILED       = 5,
    OTA_ROLLED_BACK  = 6
} ota_status_t;

/* WiFi OTA API */
int  wifi_ota_init(void);
int  wifi_ota_check_update(const char *current_version);
int  wifi_ota_start(const char *url);
void wifi_ota_set_credentials(const char *ssid, const char *password);

/* Get current OTA status */
ota_status_t wifi_ota_get_status(void);

/* Get current firmware version string */
const char* wifi_ota_get_version(void);

#ifdef __cplusplus
}
#endif

#endif /* WIFI_OTA_H */
