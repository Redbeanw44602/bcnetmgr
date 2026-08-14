#!/bin/bash

XRAY_CORE=./Xray-core
OUT=./gen

mkdir -p $OUT

find $XRAY_CORE -name '*.proto' | xargs protoc \
    -I $XRAY_CORE \
    --python_out $OUT