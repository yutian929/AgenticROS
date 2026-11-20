#!/bin/bash
# Same pinned middleware and narrow transport fix in every container.
set -euo pipefail
version=2.6.12
curl --fail --location --retry 3 "https://codeload.github.com/eProsima/Fast-DDS/tar.gz/refs/tags/v${version}" -o /tmp/fastdds.tar.gz
echo '53476bcee331f28fe83e8387122fb7f84ab39209c459bd2cc227c71e4bd21c9b  /tmp/fastdds.tar.gz' | sha256sum -c -
tar xf /tmp/fastdds.tar.gz -C /tmp
source_dir="/tmp/Fast-DDS-${version}"
patch -d "$source_dir" -p1 < /build/fastdds/exact-port-retry.patch
cmake -S "$source_dir" -B /tmp/dds-build \
    -DCMAKE_PREFIX_PATH=/opt/ros/humble -DCMAKE_INSTALL_PREFIX=/opt/fastdds \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DCOMPILE_EXAMPLES=OFF \
    -DTHIRDPARTY=OFF -DSECURITY=ON -DSQLITE3_SUPPORT=ON -DFASTDDS_STATISTICS=ON
cmake --build /tmp/dds-build -j "${DDS_BUILD_JOBS:-4}"
cmake --install /tmp/dds-build --strip
g++ -std=c++14 -I"$source_dir/src/cpp" -I/opt/fastdds/include -I/opt/ros/humble/include/fastcdr \
    /build/fastdds/reconnect.cpp -o /tmp/reconnect -L/opt/fastdds/lib -L/opt/ros/humble/lib \
    -lfastrtps -lfastcdr -lssl -lcrypto -lpthread
LD_LIBRARY_PATH=/opt/fastdds/lib:/opt/ros/humble/lib timeout 30 /tmp/reconnect
mkdir -p /opt/fastdds/share/agenticros
cp /build/fastdds/exact-port-retry.patch /opt/fastdds/share/agenticros/
