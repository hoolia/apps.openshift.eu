#!/bin/sh
set -eu

cd /src/gengetopt
./configure --prefix=/usr/local --disable-doc
make -j"$(nproc)"
make install

cmake -S /src/jansson -B /src/jansson/build \
    -DCMAKE_INSTALL_PREFIX=/opt/janus -DCMAKE_INSTALL_RPATH=/opt/janus/lib \
    -DCMAKE_BUILD_TYPE=Release \
    -DJANSSON_BUILD_SHARED_LIBS=ON -DJANSSON_BUILD_DOCS=OFF \
    -DJANSSON_EXAMPLES=OFF -DJANSSON_WITHOUT_TESTS=ON
cmake --build /src/jansson/build --parallel "$(nproc)"
cmake --install /src/jansson/build

cmake -S /src/libconfig -B /src/libconfig/build \
    -DCMAKE_INSTALL_PREFIX=/opt/janus -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_INSTALL_RPATH=/opt/janus/lib \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON \
    -DBUILD_CXX=OFF -DBUILD_EXAMPLES=OFF -DBUILD_TESTS=OFF
cmake --build /src/libconfig/build --parallel "$(nproc)"
cmake --install /src/libconfig/build

meson setup /src/libnice/build /src/libnice \
    --prefix=/opt/janus --libdir=lib --buildtype=release --default-library=shared \
    -Dcrypto-library=openssl -Dgupnp=disabled -Dgstreamer=disabled \
    -Dintrospection=disabled -Dgtk_doc=disabled -Dexamples=disabled -Dtests=disabled
meson compile -C /src/libnice/build
meson install -C /src/libnice/build

meson setup /src/libsrtp/build /src/libsrtp \
    --prefix=/opt/janus --libdir=lib --buildtype=release --default-library=shared \
    -Dcrypto-library=openssl -Dtests=disabled -Dpcap-tests=disabled -Ddoc=disabled
meson compile -C /src/libsrtp/build
meson install -C /src/libsrtp/build

meson setup /src/usrsctp/build /src/usrsctp \
    --prefix=/opt/janus --libdir=lib --buildtype=release --default-library=shared \
    -Dsctp_build_programs=false -Dsctp_inet=false -Dsctp_inet6=false
meson compile -C /src/usrsctp/build
meson install -C /src/usrsctp/build

cmake -S /src/libwebsockets -B /src/libwebsockets/build \
    -DCMAKE_INSTALL_PREFIX=/opt/janus -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_INSTALL_RPATH=/opt/janus/lib \
    -DCMAKE_BUILD_TYPE=Release -DLWS_WITH_STATIC=OFF -DLWS_WITH_SHARED=ON \
    -DLWS_MAX_SMP=1 -DLWS_WITHOUT_EXTENSIONS=OFF -DLWS_WITHOUT_TESTAPPS=ON \
    -DLWS_WITH_HTTP2=OFF -DLWS_WITH_HTTP3=OFF -DLWS_ROLE_QUIC=OFF
cmake --build /src/libwebsockets/build --parallel "$(nproc)"
cmake --install /src/libwebsockets/build

cd /src/janus
sh autogen.sh
CPPFLAGS=-I/opt/janus/include LDFLAGS="-L/opt/janus/lib $LDFLAGS" \
./configure --prefix=/usr/local --libdir=/usr/local/lib --sysconfdir=/etc \
    --disable-docs --disable-post-processing --disable-turn-rest-api \
    --disable-all-plugins --enable-plugin-videoroom \
    --disable-all-transports --enable-websockets \
    --disable-all-handlers --enable-websockets-event-handler \
    --disable-all-loggers --enable-data-channels --enable-libsrtp2
make -j"$(nproc)"
make install DESTDIR=/runtime
mkdir -p /runtime/usr/local/lib/janus/loggers
mkdir -p /runtime/opt/janus/lib
cp -a /opt/janus/lib/lib*.so* /runtime/opt/janus/lib/
install -Dm644 /src/source-lock.txt /runtime/usr/local/share/janus/source-lock.txt
for entry in jansson:LICENSE libconfig:COPYING.LIB libnice:COPYING \
    libnice:COPYING.LGPL libnice:COPYING.MPL libsrtp:LICENSE \
    usrsctp:LICENSE.md libwebsockets:LICENSE janus:COPYING; do
    name=${entry%%:*}
    file=${entry#*:}
    install -Dm644 "/src/$name/$file" "/runtime/usr/local/share/licenses/$name/$file"
done
test -f /runtime/usr/local/lib/janus/plugins/libjanus_videoroom.so
test -f /runtime/usr/local/lib/janus/transports/libjanus_websockets.so
test -f /runtime/usr/local/lib/janus/events/libjanus_wsevh.so
