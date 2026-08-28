# defrag95 UI - the CMake path, for anyone who would rather not use the
# hand-rolled Makefile next to this file. Both produce the same binary.
cmake_minimum_required(VERSION 3.14)
project(defrag95_ui CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)
FetchContent_Declare(
    tvision
    GIT_REPOSITORY https://github.com/magiblot/tvision.git
    GIT_TAG master
)
set(TV_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(tvision)

add_executable(defrag95 src/defrag95.cpp)
target_include_directories(defrag95 PRIVATE src)
target_link_libraries(defrag95 PRIVATE tvision)
