#!/usr/bin/env python

import sys, re, time, os, subprocess, tempfile, shutil, threading, traceback
import hglib
from misc import *

g_filename = g_hglib_client = g_revs = g_vim2server_fifo = g_server2vim_fifo = None
# Values are 0-based: 
g_rev2loglinenum = None

class RevInfo(object):

	pass

# Line numbers in here are 0-based. 
def init_rev2loglinenum(revinfos_):
	global g_rev2loglinenum
	g_rev2loglinenum = {}
	linenum = 0
	for revinfo in revinfos_:
		g_rev2loglinenum[revinfo.rev] = linenum
		linenum += len(revinfo.log_lines)

def init_revs(revinfos_):
	global g_revs
	g_revs = []
	for revinfo in revinfos_:
		g_revs.append(revinfo.rev)

def create_vim_function_file():
	contents = r'''
function! HgFlipbookSwitchRevision(next_or_prev, n) 
	1 wincmd w
	let log_linenum = line('.')
	2 wincmd w
	let target_linenum = line('.')
	let request = log_linenum . '|' . target_linenum . '|' . a:next_or_prev . '|' . a:n
	call writefile([request], $HG_FLIPBOOK_VIM2SERVER_FIFO)
	let response = readfile($HG_FLIPBOOK_SERVER2VIM_FIFO)[0]
	if response == 'error'
		echo 'Error.'
	else
		let response_splits = split(response, '|')
		let new_filename = response_splits[0]
		let new_linenum = response_splits[1]
		let new_log_linenum = response_splits[2]
		1 wincmd w
		execute 'edit'
		call cursor(new_log_linenum, col('.'))
		2 wincmd w
		execute 'edit' new_filename
		call cursor(new_linenum, col('.'))
	endif
endfunction

map <C-k> : call HgFlipbookSwitchRevision('prev', 1)  <CR>
map <C-j> : call HgFlipbookSwitchRevision('next', 1)  <CR>
map <C-p> : call HgFlipbookSwitchRevision('prev', 20) <CR>
map <C-n> : call HgFlipbookSwitchRevision('next', 20) <CR>
'''
	filename = os.path.join(g_tmpdir, 'vim-functions')
	with open(filename, 'w') as fout:
		fout.write(contents)
		return filename

def get_rev_filename(rev_):
	return os.path.join(g_tmpdir, 'revision-%s' % rev_)

def write_rev_to_file(rev_):
	filename = get_rev_filename(rev_)
	with open(filename, 'w') as fout:
		args = ['hg', 'cat', '-r', rev_, g_filename]
		hg_cat_output = subprocess.check_call(args, stdout=fout)
		return filename

def get_log_filename():
	return os.path.join(g_tmpdir, 'log')

def get_terminal_width():
	try:
		return int(subprocess.check_output(['stty', 'size']).rstrip().split(' ')[1])
	except:
		return 80

def write_virgin_log_file(revinfos_):
	max_line_width = get_terminal_width()-20
	filename = get_log_filename()
	with open(filename, 'w') as fout:
		for revinfo in revinfos_:
			for line in revinfo.log_lines:
				print >> fout, '    %s' % line[:max_line_width]

# return line number that was highlighted for rev_.   1-based.
def highlight_rev_in_log_file(rev_):
	linenum_to_highlight = g_rev2loglinenum[rev_]
	log_filename = get_log_filename()
	temp_filename = log_filename+'.tmp'
	with open(temp_filename, 'w') as tmpfile_fout:
		with open(log_filename) as fin:
			for linei, line in enumerate(fin):
				line = line.rstrip('\r\n')
				line_prefix = ('--> ' if linei == linenum_to_highlight else ' '*4)
				print >> tmpfile_fout, line_prefix + line[4:]
	os.rename(temp_filename, log_filename)
	return linenum_to_highlight + 1

def init_hglib_client():
	global g_hglib_client
	g_hglib_client = hglib.open('.')

# TODO: use hglib for this instead. 
def get_revinfos():
	delim = '___64576e96-ce85-4778-ab02-496fb264b41f___'
	args = ['hg', 'log', '--graph', '--template', 
			'%(delim)s{rev}%(delim)s:{node|short} {date|shortdate} {author|user} {desc|firstline}' % {'delim': delim}, g_filename]
	hg_log_output = subprocess.check_output(args)
	revinfos = []
	cur_revinfo = None
	for line in hg_log_output.splitlines():
		if delim in line:
			cur_revinfo = RevInfo()
			revinfos.append(cur_revinfo)
			cur_revinfo.rev = re.search(r'%(delim)s(\d+)%(delim)s' % {'delim': delim}, line).group(1)
			cur_revinfo.log_lines = [re.sub(delim, '', line)]
		else:
			if cur_revinfo is not None:
				cur_revinfo.log_lines.append(line)

	if not revinfos:
		sys.exit('Found no revisions.')

	return revinfos

def init_tmpdir():
	global g_tmpdir
	g_tmpdir = tempfile.mkdtemp('-hg-flipbook')

def init_fifos():
	global g_vim2server_fifo, g_server2vim_fifo
	g_vim2server_fifo = os.path.join(g_tmpdir, 'vim2server')
	g_server2vim_fifo = os.path.join(g_tmpdir, 'server2vim')
	os.mkfifo(g_vim2server_fifo)
	os.mkfifo(g_server2vim_fifo)
	os.environ['HG_FLIPBOOK_VIM2SERVER_FIFO'] = g_vim2server_fifo
	os.environ['HG_FLIPBOOK_SERVER2VIM_FIFO'] = g_server2vim_fifo

def start_server_thread():
	def run():
		while True:
			with open(os.environ['HG_FLIPBOOK_VIM2SERVER_FIFO']) as fin:
				request = fin.read()
			try:
				response = get_response(request)
			except:
				traceback.print_exc(file=sys.stderr)
				sys.stderr.flush()
				response = 'error'
			with open(os.environ['HG_FLIPBOOK_SERVER2VIM_FIFO'], 'w') as fout:
				fout.write(response)
	thread = threading.Thread(target=run)
	thread.daemon = True
	thread.start()

def get_response(request_):
	orig_log_linenum, orig_target_linenum, next_or_prev, n = request_.rstrip().split('|')
	orig_log_linenum = int(orig_log_linenum)
	orig_target_linenum = int(orig_target_linenum)
	n = int(n)
	next_aot_prev = {'next': True, 'prev': False}[next_or_prev]
	cur_rev = get_cur_rev(orig_log_linenum)
	rev_offset = n*(1 if next_aot_prev else -1)
	upcoming_rev = get_upcoming_rev(cur_rev, rev_offset)
	upcoming_rev_filename = get_filename_of_rev_creating_if_necessary(upcoming_rev)
	highlighted_log_linenum = highlight_rev_in_log_file(upcoming_rev)
	upcoming_linenum = get_new_linenum(orig_target_linenum, cur_rev, upcoming_rev)
	return '%s|%d|%d' % (upcoming_rev_filename, upcoming_linenum, highlighted_log_linenum)

def main():
	revinfos = get_revinfos()
	init_rev2loglinenum(revinfos)
	init_revs(revinfos)
	init_hglib_client()
	init_tmpdir()
	init_fifos()
	init_rev = revinfos[0].rev
	write_virgin_log_file(revinfos)
	highlighted_log_linenum = highlight_rev_in_log_file(init_rev)
	start_server_thread()
	args = ['vim', '-c', 'source '+create_vim_function_file(), 
			'-c', 'set readonly', '-c', 'resize 10', 
			'-c', 'call cursor(%d,1)' % highlighted_log_linenum, 
			'-c', '2 wincmd w', '-c', 'set readonly', '-o', get_log_filename(), write_rev_to_file(init_rev)]
	sys.stderr = open(os.path.join(g_tmpdir, 'stderr'), 'w')
	subprocess.call(args)
	shutil.rmtree(g_tmpdir)

def get_filename_of_rev_creating_if_necessary(rev_):
	filename = get_rev_filename(rev_)
	if not os.path.exists(filename):
		filename_according_to_write = write_rev_to_file(rev_)
		assert filename_according_to_write == filename
	return filename

# arg orig_log_linenum_ - 1-based.
def get_cur_rev(orig_log_linenum_):
	for rev in g_revs:
		log_linenum = g_rev2loglinenum[rev]
		if log_linenum > orig_log_linenum_-1:
			break
		r = rev
	return r

def get_upcoming_rev(cur_rev_, rev_offset_):
	upcoming_rev_idx = g_revs.index(cur_rev_) + rev_offset_
	upcoming_rev_idx = rein_in(upcoming_rev_idx, 0, len(g_revs)-1)
	return g_revs[upcoming_rev_idx]

class Hunk(object):

	def __init__(self, rev1_startline_, rev2_startline_, num_lines_added_):
		self.rev1_startline = rev1_startline_
		self.rev2_startline = rev2_startline_
		self.num_lines_added = num_lines_added_

	def __str__(self):
		return 'Hunk%s' % (self.rev1_startline, self.rev2_startline, self.num_lines_added).__str__()

	def __repr__(self):
		return self.__str__()

	def tuple(self):
		return (self.rev1_startline, self.rev2_startline, self.num_lines_added)

def get_hunks_cache_filename(rev1_, rev2_):
	return os.path.join(g_tmpdir, 'hunks-%s-to-%s' % (rev1_, rev2_))

def get_diff_hunks_from_cache(rev1_, rev2_):
	filename = get_hunks_cache_filename(rev1_, rev2_)
	try:
		with open(filename) as fin:
			hunk_tuples = eval(fin.read())
		return [Hunk(*hunk_tuple) for hunk_tuple in hunk_tuples]
	except IOError:
		return None

def write_hunks_cache_file(hunks_, rev1_, rev2_):
	filename = get_hunks_cache_filename(rev1_, rev2_)
	with open(filename, 'w') as fout:
		fout.write(repr([hunk.tuple() for hunk in hunks_]))

def get_diff_hunks(rev1_, rev2_):
	r = get_diff_hunks_from_cache(rev1_, rev2_)
	if r is None:
		r = get_diff_hunks_from_hg(rev1_, rev2_)
		write_hunks_cache_file(r, rev1_, rev2_)
	return r

def get_diff_hunks_from_hg(rev1_, rev2_):
	args = ['hg', 'diff', '-U', '0', '-r', '%s:%s' % (rev1_, rev2_), g_filename]
	proc = subprocess.Popen(args, stdout=subprocess.PIPE)
	r = []
	for line in proc.stdout:
		mo = re.match(r'^@@ \-(\d+),(\d+) \+(\d+),(\d+) .*@@$', line)
		if mo:
			rev1_startline = int(mo.group(1))
			rev2_startline = int(mo.group(3))
			num_lines_added = int(mo.group(4)) - int(mo.group(2))
			if num_lines_added != 0:
				r.append(Hunk(rev1_startline, rev2_startline, num_lines_added))
	proc.wait()
	if proc.returncode != 0:
		raise Exception('hg returned %d' % proc.returncode)
	return r

def get_new_linenum_via_hunks(hunks_, orig_linenum_):
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

def get_new_linenum(orig_linenum_, cur_rev_, upcoming_rev_):
	hunks = get_diff_hunks(cur_rev_, upcoming_rev_)
	r = get_new_linenum_via_hunks(hunks, orig_linenum_)
	return r

if __name__ == '__main__':

	if len(sys.argv) == 2:
		g_filename = sys.argv[1]
		main()
	else:
		sys.exit("Don't understand arguments.")

