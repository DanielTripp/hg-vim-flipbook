#!/usr/bin/env python

import sys, re, subprocess, time, os, tempfile, shutil
from misc import *

class Hunk(object):

	def __init__(self, rev1_startline_, rev2_startline_, num_lines_added_):
		self.rev1_startline = rev1_startline_
		self.rev2_startline = rev2_startline_
		self.num_lines_added = num_lines_added_

	def __str__(self):
		return 'Hunk%s' % (self.rev1_startline, self.rev2_startline, self.num_lines_added).__str__()

	def __repr__(self):
		return self.__str__()

def get_diff_hunks(filename_, rev1_, rev2_):
	args = ['hg', 'diff', '-U', '0', '-r', '%s:%s' % (rev1_, rev2_)]
	hg_diff_output = subprocess.check_output(args)
	print hg_diff_output # tdr 
	r = []
	for line in hg_diff_output.splitlines():
		mo = re.match(r'@@ \-(\d+),(\d+) \+(\d+),(\d+) .*@@', line)
		if mo:
			rev1_startline = int(mo.group(1))
			rev2_startline = int(mo.group(3))
			num_lines_added = int(mo.group(4)) - int(mo.group(2))
			if num_lines_added != 0:
				r.append(Hunk(rev1_startline, rev2_startline, num_lines_added))
	return r

def get_new_linenum(hunks_, orig_linenum_):
	offset = 0
	for hunk in hunks_:
		if hunk.rev1_startline > orig_linenum_:
			break
		if hunk.num_lines_added >= 0:
			offset += hunk.num_lines_added
		else:
			if orig_linenum_ + offset < hunk.rev2_startline + abs(hunk.num_lines_added):
				offset += hunk.rev2_startline - (orig_linenum_ + offset)
			else:
				offset += hunk.num_lines_added
	return orig_linenum_ + offset

if __name__ == '__main__':

	if len(sys.argv) == 5:
		filename, rev1, rev2 = sys.argv[1:-1]
		linenum = int(sys.argv[-1])
		hunks = get_diff_hunks(filename, rev1, rev2)
		print get_new_linenum(hunks, linenum)
	else:
		raise Exception()


