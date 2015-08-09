#!/usr/bin/env python

import sys, subprocess, re, time, curses

if len(sys.argv) < 2:
	sys.exit('Need a filename argument.')

filename = sys.argv[1]

class RevInfo(object):

	pass

def change_attrs(win_, y_, newattrs_):
	numchars = win_.getmaxyx()[1]
	x = 0
	text = win_.instr(y_, x, numchars)
	win_.addstr(y_, x, text, newattrs_)

def reverse_line(win_, y_):
	change_attrs(win_, y_, curses.A_REVERSE)
	win_.refresh()

def unreverse_line(win_, y_):
	change_attrs(win_, y_, 0)
	win_.refresh()

def unreverse_revinfo(win_, revinfos_, revinfo_idx_):
	change_revinfo_reversal(win_, revinfos_, revinfo_idx_, False)

def reverse_revinfo(win_, revinfos_, revinfo_idx_):
	change_revinfo_reversal(win_, revinfos_, revinfo_idx_, True)

def change_revinfo_reversal(win_, revinfos_, revinfo_idx_, reverse_aot_unreverse_):
	line = 0
	for revinfo_idx, revinfo in enumerate(revinfos_):
		if revinfo_idx == revinfo_idx_:
			if reverse_aot_unreverse_:
				reverse_line(win_, line)
			else:
				unreverse_line(win_, line)
			break
		else:
			line += len(revinfo.lines_for_gui)
	else:
		raise Exception()

fout = open('/tmp/out.txt', 'w') # tdr 

def show_tui(revinfos_):
	assert len(revinfos_)
	def curses_main(stdscr_):
		y = 0
		curses.curs_set(0)
		for revinfo in revinfos_:
			for line in revinfo.lines_for_gui:
				stdscr_.addstr(y, 0, line)
				y += 1
		stdscr_.refresh()
		selected_revinfo_idx = 0
		reverse_line(stdscr_, selected_revinfo_idx)
		while True:
			gotten_char = stdscr_.getch()
			if gotten_char == ord('\n'):
				break
			new_selected_revinfo_idx = selected_revinfo_idx
			if gotten_char == ord('k') and new_selected_revinfo_idx>0:
				new_selected_revinfo_idx -= 1
			elif gotten_char == ord('j') and new_selected_revinfo_idx<len(revinfos_)-1:
				new_selected_revinfo_idx += 1
			if new_selected_revinfo_idx != selected_revinfo_idx:
				unreverse_revinfo(stdscr_, revinfos_, selected_revinfo_idx)
				reverse_revinfo(stdscr_, revinfos_, new_selected_revinfo_idx)
				selected_revinfo_idx = new_selected_revinfo_idx
	curses.wrapper(curses_main)

if __name__ == '__main__':

	delim = '___64576e96-ce85-4778-ab02-496fb264b41f___'
	args = ['hg', 'log', '--graph', '--template', 
			'%(delim)s{rev}%(delim)s:{node|short} {date|shortdate} {author|user} {desc|firstline}' % {'delim': delim}, filename]
	hg_log_output = subprocess.check_output(args)
	revinfos = []
	cur_revinfo = None
	for line in hg_log_output.splitlines():
		if delim in line:
			cur_revinfo = RevInfo()
			revinfos.append(cur_revinfo)
			cur_revinfo.rev = re.search(r'%(delim)s(\d+)%(delim)s' % {'delim': delim}, line).group(1)
			cur_revinfo.lines_for_gui = [re.sub(delim, '', line)]
		else:
			if cur_revinfo is not None:
				cur_revinfo.lines_for_gui.append(line)

	if not revinfos:
		sys.exit('Found no revisions.')

	show_tui(revinfos)

