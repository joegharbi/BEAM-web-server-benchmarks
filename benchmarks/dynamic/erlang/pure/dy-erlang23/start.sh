#!/bin/sh
ulimit -n 100000
exec erl -noshell -pa /app -eval 'c:l(hello), hello:main([]), init:stop().' 