#!/usr/bin/env python

with open('/tmp/in.txt') as fin:
	for line in fin:
		print [ord(ch) for ch in line]

