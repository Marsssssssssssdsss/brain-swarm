cmake_minimum_required(VERSION 3.20)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)

set(EXTRA_COMPONENT_DIRS
    components/tflite-micro
    components/cmsis-dsp
)

set(CONFIG_FREERTOS_HZ 1000)

set(SUPPORTED_TARGETS esp32s3)
set(PROJECT_VER "1.0.0")

project(neuro_resonator_ai)

target_compile_options(${PROJECT_NAME} PUBLIC
    -O2
    -std=gnu17
    -Wno-address-of-packed-member
)

idf_build_set_property(EXTRA_FLAGS "-march=rv32imc_zicsr_zifencei" FOR "esp32s3")
