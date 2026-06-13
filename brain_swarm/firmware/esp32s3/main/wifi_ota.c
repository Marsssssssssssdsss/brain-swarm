#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_https_ota.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "wifi_ota.h"

static const char *TAG = "wifi_ota";

/* NVS keys */
#define NVS_NAMESPACE       "wifi_ota"
#define NVS_KEY_SSID        "wifi_ssid"
#define NVS_KEY_PASS        "wifi_pass"
#define NVS_KEY_VERSION     "fw_version"

/* Event group bits */
#define WIFI_CONNECTED_BIT  BIT0
#define WIFI_FAIL_BIT       BIT1

/* Internal state */
static struct {
    char       ssid[WIFI_MAX_SSID_LEN + 1];
    char       password[WIFI_MAX_PASS_LEN + 1];
    char       version[32];
    ota_status_t status;
    int        retry_count;
    EventGroupHandle_t event_group;
} ota_state;

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                                int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ota_state.retry_count++;
        if (ota_state.retry_count < WIFI_MAX_RETRIES) {
            ESP_LOGW(TAG, "WiFi disconnect (attempt %d/%d)",
                     ota_state.retry_count, WIFI_MAX_RETRIES);
            esp_wifi_connect();
        } else {
            xEventGroupSetBits(ota_state.event_group, WIFI_FAIL_BIT);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        ota_state.retry_count = 0;
        xEventGroupSetBits(ota_state.event_group, WIFI_CONNECTED_BIT);
    }
}

static int connect_wifi(void)
{
    if (strlen(ota_state.ssid) == 0) {
        ESP_LOGE(TAG, "WiFi SSID not configured");
        return -1;
    }

    ota_state.event_group = xEventGroupCreate();
    if (!ota_state.event_group) {
        ESP_LOGE(TAG, "Failed to create event group");
        return -1;
    }

    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t instance_any_id;
    esp_event_handler_instance_t instance_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT,
                        ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, &instance_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT,
                        IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, &instance_got_ip));

    wifi_config_t wifi_config = {
        .sta = {
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    strncpy((char *)wifi_config.sta.ssid, ota_state.ssid, sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char *)wifi_config.sta.password, ota_state.password, sizeof(wifi_config.sta.password) - 1);

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Connecting to WiFi SSID: %s", ota_state.ssid);

    EventBits_t bits = xEventGroupWaitBits(ota_state.event_group,
                        WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                        pdFALSE, pdFALSE,
                        pdMS_TO_TICKS(WIFI_CONNECT_TIMEOUT_MS));

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "WiFi connected");
        return 0;
    } else if (bits & WIFI_FAIL_BIT) {
        ESP_LOGE(TAG, "WiFi connection failed after %d retries", WIFI_MAX_RETRIES);
        return -1;
    } else {
        ESP_LOGE(TAG, "WiFi connection timeout (%dms)", WIFI_CONNECT_TIMEOUT_MS);
        return -1;
    }
}

static void disconnect_wifi(void)
{
    esp_wifi_disconnect();
    esp_wifi_stop();
    esp_wifi_deinit();
    esp_event_loop_delete_default();
    esp_netif_deinit();

    if (ota_state.event_group) {
        vEventGroupDelete(ota_state.event_group);
        ota_state.event_group = NULL;
    }
}

static int http_ota_update(const char *url)
{
    ESP_LOGI(TAG, "Starting OTA from: %s", url);

    esp_http_client_config_t http_config = {
        .url              = url,
        .timeout_ms       = OTA_CONNECT_TIMEOUT_MS,
        .keep_alive_enable = false,
        .skip_cert_common_name_check = true,
    };

    esp_https_ota_config_t ota_config = {
        .http_config = &http_config,
    };

    esp_https_ota_handle_t ota_handle = NULL;
    esp_err_t err = esp_https_ota_begin(&ota_config, &ota_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "OTA begin failed: %s", esp_err_to_name(err));
        return -1;
    }

    /* Download and flash in chunks */
    while (1) {
        err = esp_https_ota_perform(ota_handle);
        if (err == ESP_ERR_HTTPS_OTA_IN_PROGRESS) {
            int progress = esp_https_ota_get_image_size(ota_handle);
            ESP_LOGI(TAG, "OTA progress: %d bytes", progress);
            continue;
        }
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "OTA perform failed: %s", esp_err_to_name(err));
            esp_https_ota_abort(ota_handle);
            return -1;
        }
        break;
    }

    /* Finish OTA and validate */
    err = esp_https_ota_finish(ota_handle);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "OTA update successful, restarting...");
        ota_state.status = OTA_SUCCESS;
        return 0;
    } else {
        ESP_LOGE(TAG, "OTA finish failed: %s", esp_err_to_name(err));
        if (err == ESP_ERR_OTA_ROLLBACK_FAILED) {
            ota_state.status = OTA_ROLLED_BACK;
        } else {
            ota_state.status = OTA_FAILED;
        }
        return -1;
    }
}

int wifi_ota_init(void)
{
    memset(&ota_state, 0, sizeof(ota_state));
    ota_state.status = OTA_IDLE;
    strcpy(ota_state.version, "1.0.0");

    /* Open NVS and load credentials */
    nvs_handle_t nvs_handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &nvs_handle);
    if (err == ESP_OK) {
        size_t len = sizeof(ota_state.ssid);
        nvs_get_str(nvs_handle, NVS_KEY_SSID, ota_state.ssid, &len);

        len = sizeof(ota_state.password);
        nvs_get_str(nvs_handle, NVS_KEY_PASS, ota_state.password, &len);

        len = sizeof(ota_state.version);
        nvs_get_str(nvs_handle, NVS_KEY_VERSION, ota_state.version, &len);

        nvs_close(nvs_handle);
    }

    /* Mark current app as valid (in case of rollback) */
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t ota_state_part;
    if (esp_ota_get_state_partition(running, &ota_state_part) == ESP_OK) {
        if (ota_state_part == ESP_OTA_IMG_PENDING_VERIFY) {
            esp_ota_mark_app_valid_cancel_rollback();
        }
    }

    ESP_LOGI(TAG, "OTA initialized, version: %s", ota_state.version);
    return 0;
}

int wifi_ota_check_update(const char *current_version)
{
    if (!current_version)
        current_version = ota_state.version;

    if (connect_wifi() != 0) {
        ESP_LOGW(TAG, "Cannot check OTA: WiFi not connected");
        return -1;
    }

    /* Construct version check URL */
    char check_url[256];
    snprintf(check_url, sizeof(check_url),
             "%sversion_check?v=%s", OTA_URL_BASE, current_version);

    ESP_LOGI(TAG, "Checking for updates: %s", check_url);

    esp_http_client_config_t http_config = {
        .url              = check_url,
        .timeout_ms       = 10000,
        .skip_cert_common_name_check = true,
    };
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    if (!client) {
        ESP_LOGE(TAG, "Failed to init HTTP client");
        disconnect_wifi();
        return -1;
    }

    esp_err_t err = esp_http_client_perform(client);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Version check HTTP failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        disconnect_wifi();
        return -1;
    }

    int status_code = esp_http_client_get_status_code(client);
    int64_t content_length = esp_http_client_get_content_length(client);

    if (status_code == 200 && content_length > 0) {
        ota_state.status = OTA_AVAILABLE;
        ESP_LOGI(TAG, "Update available (status=%d, size=%lld)", status_code, content_length);
    } else {
        ESP_LOGI(TAG, "No update available (status=%d)", status_code);
        ota_state.status = OTA_IDLE;
    }

    esp_http_client_cleanup(client);
    disconnect_wifi();
    return (ota_state.status == OTA_AVAILABLE) ? 1 : 0;
}

int wifi_ota_start(const char *url)
{
    if (!url) {
        ESP_LOGE(TAG, "No OTA URL provided");
        return -1;
    }

    ota_state.status = OTA_DOWNLOADING;

    if (connect_wifi() != 0) {
        ota_state.status = OTA_FAILED;
        return -1;
    }

    int ret = http_ota_update(url);

    disconnect_wifi();

    if (ret == 0) {
        /* Success - restart to boot new firmware */
        esp_restart();
    }

    return ret;
}

void wifi_ota_set_credentials(const char *ssid, const char *password)
{
    if (!ssid || !password)
        return;

    strncpy(ota_state.ssid, ssid, WIFI_MAX_SSID_LEN);
    strncpy(ota_state.password, password, WIFI_MAX_PASS_LEN);

    nvs_handle_t nvs_handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &nvs_handle) == ESP_OK) {
        nvs_set_str(nvs_handle, NVS_KEY_SSID, ota_state.ssid);
        nvs_set_str(nvs_handle, NVS_KEY_PASS, ota_state.password);
        nvs_commit(nvs_handle);
        nvs_close(nvs_handle);
    }

    ESP_LOGI(TAG, "WiFi credentials saved to NVS");
}

ota_status_t wifi_ota_get_status(void)
{
    return ota_state.status;
}

const char* wifi_ota_get_version(void)
{
    return ota_state.version;
}
