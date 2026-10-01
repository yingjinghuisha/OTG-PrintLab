cmake_minimum_required(VERSION 3.22.1)
project("otgprint" CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(otgprint SHARED
        pcl3gui_encoder.cpp
        jni_api.cpp)

target_link_libraries(otgprint
        android
        log)
