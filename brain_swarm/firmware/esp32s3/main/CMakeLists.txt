idf_component_register(
    SRCS
        main.c
        uart_protocol.c
        focus_inference.c
        fft_processor.c
        wifi_ota.c
        closed_loop.c
    INCLUDE_DIRS
        ./
        ./include
    REQUIRES
        tflite-micro
        cmsis-dsp
        nvs_flash
        esp_wifi
        esp_netif
        esp_https_ota
        esp_event
        driver
        freertos
        esp_timer
        esp_sleep
)

if(CONFIG_IDF_TARGET_ESP32S3)
    target_compile_options(${COMPONENT_LIB} PRIVATE -O3 -ftree-vectorize)
endif()
