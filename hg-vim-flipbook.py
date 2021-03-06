#!/usr/bin/env python

import sys, re, time, os, subprocess, tempfile, shutil, threading, traceback, difflib
import mercurial
sys.path.append(os.path.dirname(__file__))
import hglib
from misc import *

LOG = False

g_filename = g_hglib_client = g_revs = g_vim2server_fifo = g_server2vim_fifo = g_mem_cached_rev = g_mem_cached_rev_contents_lines = None
g_cur_rev = None
g_history_back_revs = []; g_history_forward_revs = []
# Values are 0-based: 
g_rev2loglinenum = None
g_standalone_aot_extension = None
g_extension_ui = None

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

# Note [1]: "jogging the cursor" here w/ ^$ is a work-around for a bug that appeared
# on vim on a certain machine, where if the user switched many revisions (for
# example 20) such that the window should have scrolled for the log file when
# we update the cursor for it, the cursor position was updating ok but the
# window wasn't scrolling, so after this function exits, the user would still
# be looking at the old revision in the log window, now un-highlighted.
# Manually switching to the log buffer after would scroll the window
# appropriately.
# 
# (The machine was named 'u', on Cirrus hosting, Ubuntu, vim 7.3.)
# 
# Also executing "zz" here because in some cases, pressing Ctrl-J Ctrl-K Ctrl-J 
# Ctrl-K etc.  would not keep the highlighted line centered visually.  I don't 
# understand why, but it seems to be at least partly due to log window buffer 
# height.  (Happened w/ height of 10 on my Ubuntu virtual box (vim 7.3), height 
# of 20 on the 'u' Cirrus machine, also Ubuntu, vim 7.3.)
def create_vim_function_file():
	contents = r'''
function! HgVimFlipbookSwitchRevision(next_or_prev, repeat_count) 
	1 wincmd w
	let log_linenum = line('.')
	2 wincmd w
	let target_linenum = line('.')

	" repeat_count will be 0 if this command is invoked without a COUNT argument preceeding it. 
	" We should move 1 revision in this case. 
	" repeat_count will be 1 if the command is invokved with a COUNT argument of 1 preceeding it.  
	" We should move 1 revision in this case too.
	let n = max([1, a:repeat_count])

	" This allows the user to switch to the log window and manually move the cursor 
	" to the line for the desired revision (perhaps by searching for the rev or 
	" nodeid, or commit comment text) and then (as long as their cursor isn't on the 
	" first column) when they invoke "next" or "prev" the revision will switch to the 
	" one for the line they're on - not the next or previous one.  So in this case 
	" "next" and "prev" act as a "go to THIS revision" function.  Another approach 
	" would have been to create a new function and key mapping for this 
	" functionality, but I'd rather keep it simple.
	" If the user never moves their cursor manually in the log window, then it will 
	" remain on column 1, and this code won't do anything.
	1 wincmd w
	if col('.') != 1
		let n = n - 1
	endif

	let request = 'next-or-prev|' . log_linenum . '|' . target_linenum . '|' . a:next_or_prev . '|' . n
	call HgVimFlipbookWriteRequestAndReadAndDealWithResponse(request)
endfunction

" Thanks to http://vim.wikia.com/wiki/Invoke_a_function_with_a_count_prefix 
command! -nargs=1 HgVimFlipbookSwitchRevisionPrevCmd call HgVimFlipbookSwitchRevision('prev', <args>)
map <C-k> : <C-U>HgVimFlipbookSwitchRevisionPrevCmd(v:count)<CR>
command! -nargs=1 HgVimFlipbookSwitchRevisionNextCmd call HgVimFlipbookSwitchRevision('next', <args>)
map <C-j> : <C-U>HgVimFlipbookSwitchRevisionNextCmd(v:count)<CR>

function! HgVimFlipbookMoveThroughHistory(forward_or_back)
	2 wincmd w
	let target_linenum = line('.')
	let request = 'forward-or-back|' . target_linenum . '|' . a:forward_or_back
	call HgVimFlipbookWriteRequestAndReadAndDealWithResponse(request)
endfunction

function! HgVimFlipbookWriteRequestAndReadAndDealWithResponse(request)
	call writefile([a:request], $HG_VIM_FLIPBOOK_VIM2SERVER_FIFO)
	let response = readfile($HG_VIM_FLIPBOOK_SERVER2VIM_FIFO)[0]
	if response == 'error'
		echo 'Error.'
	elseif response == 'do-nothing'
		2 wincmd w
		echo 
		" ^^ Doing this echo because otherwise the name of the command shows up in vim's status line 
		" eg. ":HgVimFlipbookSwitchRevisionPrevCmd(v:count)" and I think that's useless and ugly. 
		" We don't have to do this echo if we switch revisions because then we'll be editing a new file, 
		" and the filename, number of lines, etc. will appear in the status line instead.
	else
		let response_splits = split(response, '|')
		let new_filename = response_splits[0]
		let new_linenum = response_splits[1]
		let new_log_linenum = response_splits[2]
		1 wincmd w
		execute 'edit'
		set readonly
		call cursor(new_log_linenum, 1)
		execute "normal! zz"
		execute "normal! $^"
		" ^^ See note [1] 
		2 wincmd w
		execute 'edit' new_filename
		set readonly
		call cursor(new_linenum, col('.'))
	endif
endfunction

map <C-h> : <C-U>call HgVimFlipbookMoveThroughHistory('back')<CR>
map <C-l> : <C-U>call HgVimFlipbookMoveThroughHistory('forward')<CR>

'''
	filename = os.path.join(g_tmpdir, 'vim-functions')
	with open(filename, 'w') as fout:
		fout.write(contents)
		return filename

def get_rev_filename(rev_):
	return os.path.join(g_tmpdir, 'revision-%s' % rev_)

def write_rev_to_file(rev_):
	global g_mem_cached_rev, g_mem_cached_rev_contents_lines
	file_contents = g_hglib_client.cat([g_filename], rev=rev_)
	filename = get_rev_filename(rev_)
	with open(filename, 'w') as fout:
		fout.write(file_contents)
	g_mem_cached_rev = rev_
	g_mem_cached_rev_contents_lines = file_contents.splitlines()
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

def get_revinfos():
	delim = '___64576e96-ce85-4778-ab02-496fb264b41f___'
	args = ['hg', 'log', '--graph', '--template', 
			'%(delim)s{rev}%(delim)s:{node|short} {date|shortdate} {author|user} {desc|firstline}\n\n' % {'delim': delim}, g_filename]
	try:
		hg_log_output = subprocess.check_output(args, stderr=subprocess.STDOUT)
	except subprocess.CalledProcessError:
		# Older versions of mercurial don't have the "--graph" option.
		args.remove('--graph')
		hg_log_output = subprocess.check_output(args, stderr=subprocess.STDOUT)
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
		exit_with_error('Found no revisions.')

	return revinfos

def init_tmpdir():
	global g_tmpdir
	g_tmpdir = tempfile.mkdtemp('-hg-vim-flipbook')

def init_fifos():
	global g_vim2server_fifo, g_server2vim_fifo
	g_vim2server_fifo = os.path.join(g_tmpdir, 'vim2server')
	g_server2vim_fifo = os.path.join(g_tmpdir, 'server2vim')
	os.mkfifo(g_vim2server_fifo)
	os.mkfifo(g_server2vim_fifo)
	os.environ['HG_VIM_FLIPBOOK_VIM2SERVER_FIFO'] = g_vim2server_fifo
	os.environ['HG_VIM_FLIPBOOK_SERVER2VIM_FIFO'] = g_server2vim_fifo

def log(str_):
	if LOG:
		printerr(str_)
		sys.stderr.flush()

def start_server_thread():
	def run():
		while True:
			with open(os.environ['HG_VIM_FLIPBOOK_VIM2SERVER_FIFO']) as fin:
				request = fin.read()
			try:
				t0 = time.time()
				response = get_response(request)
				log('get_response(): %d ms.' % int((time.time() - t0)*1000))
			except:
				traceback.print_exc(file=sys.stderr)
				sys.stderr.flush()
				response = 'error'
			with open(os.environ['HG_VIM_FLIPBOOK_SERVER2VIM_FIFO'], 'w') as fout:
				fout.write(response)
	thread = threading.Thread(target=run)
	thread.daemon = True
	thread.start()

def get_response(request_):
	cmd = request_.split('|', 1)[0]
	if cmd == 'next-or-prev':
		return get_response_for_next_or_prev_request(request_)
	elif cmd == 'forward-or-back':
		return get_response_for_forward_or_back_request(request_)
	else:
		return 'error'

def get_response_for_next_or_prev_request(request_):
	orig_log_linenum, orig_target_linenum, next_or_prev, n = request_.rstrip().split('|')[1:]
	orig_log_linenum = int(orig_log_linenum)
	orig_target_linenum = int(orig_target_linenum)
	n = int(n)
	next_aot_prev = {'next': True, 'prev': False}[next_or_prev]
	rev_at_log_cursor = get_rev_at_log_linenum(orig_log_linenum)
	rev_offset = n*(1 if next_aot_prev else -1)
	g_history_forward_revs[:] = []
	g_history_back_revs.append(g_cur_rev)
	upcoming_rev = get_upcoming_rev(rev_at_log_cursor, rev_offset)
	return get_response_by_upcoming_rev(upcoming_rev, orig_target_linenum)

def get_response_for_forward_or_back_request(request_):
	global g_cur_rev
	orig_target_linenum, forward_or_back = request_.rstrip().split('|')[1:]
	orig_target_linenum = int(orig_target_linenum)
	forward_aot_back = {'forward': True, 'back': False}[forward_or_back]
	upcoming_rev = None
	if forward_aot_back:
		if g_history_forward_revs:
			g_history_back_revs.append(g_cur_rev)
			upcoming_rev = g_history_forward_revs.pop(0)
	else:
		if g_history_back_revs:
			g_history_forward_revs.insert(0, g_cur_rev)
			upcoming_rev = g_history_back_revs.pop(-1)
	return get_response_by_upcoming_rev(upcoming_rev, orig_target_linenum)

def get_response_by_upcoming_rev(upcoming_rev_, orig_target_linenum_):
	global g_cur_rev
	if upcoming_rev_ in (None, g_cur_rev):
		return 'do-nothing'
	else:
		highlighted_log_linenum = highlight_rev_in_log_file(upcoming_rev_)
		upcoming_linenum = get_new_linenum(orig_target_linenum_, g_cur_rev, upcoming_rev_)
		upcoming_rev_filename = get_rev_filename(upcoming_rev_)
		assert os.path.exists(upcoming_rev_filename)
		g_cur_rev = upcoming_rev_
		return '%s|%d|%d' % (escape_filename_for_vim_arg(upcoming_rev_filename), upcoming_linenum, highlighted_log_linenum)

def hg_extension_main(ui_, repo_, filename_, **opts_):
	"""'Flip' through revisions of a file, with the help of the 'vim' editor."""
	global g_filename, g_standalone_aot_extension, g_extension_ui
	g_filename = filename_
	g_standalone_aot_extension = False
	g_extension_ui = ui_
	unimain()

def standalone_main():
	global g_filename, g_standalone_aot_extension
	g_standalone_aot_extension = True
	if len(sys.argv) == 2:
		g_filename = sys.argv[1]
		unimain()
	else:
		sys.exit("Don't understand arguments.")

def exit_with_error(msg_):
	if g_standalone_aot_extension:
		sys.exit(msg_)
	else:
		g_extension_ui.write_err(msg_+'\n')
		sys.exit(1)

def unimain():
	global g_cur_rev
	revinfos = get_revinfos()
	init_rev2loglinenum(revinfos)
	init_revs(revinfos)
	init_hglib_client()
	init_tmpdir()
	init_fifos()
	g_cur_rev = revinfos[0].rev
	write_virgin_log_file(revinfos)
	highlighted_log_linenum = highlight_rev_in_log_file(g_cur_rev)
	start_server_thread()
	args = ['vim', '-c', 'source %s' % escape_filename_for_vim_arg(create_vim_function_file()),  
			'-c', 'set readonly', '-c', 'resize 10', 
			'-c', 'call cursor(%d,1)' % highlighted_log_linenum, 
			'-c', '2 wincmd w', '-c', 'set readonly', '-o', get_log_filename(), write_rev_to_file(g_cur_rev)]
	sys.stderr = open(os.path.join(g_tmpdir, 'stderr'), 'w')
	subprocess.call(args)
	shutil.rmtree(g_tmpdir)

def escape_filename_for_vim_arg(str_):
	return str_.replace(' ', '\\ ')

# arg orig_log_linenum_ - 1-based.
def get_rev_at_log_linenum(orig_log_linenum_):
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

def get_diff_hunks_from_file_cache(rev1_, rev2_):
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

# Might as well write these.  For performance.
def write_reverse_hunks_cache_file(hunks_, rev1_, rev2_):
	reversed_hunks = get_reversed_hunks(hunks_)
	write_hunks_cache_file(reversed_hunks, rev2_, rev1_)

def get_reversed_hunks(hunks_):
	return [get_reversed_hunk(hunk) for hunk in hunks_]

def get_reversed_hunk(hunk_):
	return Hunk(hunk_.rev2_startline, hunk_.rev1_startline, -hunk_.num_lines_added)

def get_diff_hunks(rev1_, rev2_):
	r = get_diff_hunks_from_file_cache(rev1_, rev2_)
	if r is None:
		r = get_diff_hunks_from_hg(rev1_, rev2_)
		write_hunks_cache_file(r, rev1_, rev2_)
		write_reverse_hunks_cache_file(r, rev1_, rev2_)
	return r

def get_diff_hunks_from_hg(rev1_, rev2_):
	global g_mem_cached_rev, g_mem_cached_rev_contents_lines
	if rev1_ == g_mem_cached_rev:
		rev1_contents_lines = g_mem_cached_rev_contents_lines
	else:
		with open(get_rev_filename(rev1_)) as fin:
			rev1_contents_lines = fin.readlines()
	rev2_contents = g_hglib_client.cat([g_filename], rev=rev2_)
	with open(get_rev_filename(rev2_), 'w') as fout:
		fout.write(rev2_contents)
	rev2_contents_lines = rev2_contents.splitlines()
	r = []
	for line in difflib.unified_diff(rev1_contents_lines, rev2_contents_lines, n=0):
		mo = re.match(r'^@@ \-(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? .*@@$', line)
		if mo:
			rev1_startline = int(mo.group(1))
			rev2_startline = int(mo.group(3))
			rev1_numlines = int(mo.group(2) or 1)
			rev2_numlines = int(mo.group(4) or 1)
			num_lines_added = rev2_numlines - rev1_numlines

			# Thanks https://www.artima.com/weblogs/viewpost.jsp?thread=164293 
			# "If the chunk size is 0, the first number is one lower than one would 
			# expect (it is the line number after which the chunk should be inserted 
			# or deleted; in all other cases it gives the first line number or the 
			# replaced range of lines)."
			if rev1_numlines == 0:
				rev1_startline += 1
			if rev2_numlines == 0:
				rev2_startline += 1

			if num_lines_added != 0:
				r.append(Hunk(rev1_startline, rev2_startline, num_lines_added))
	g_mem_cached_rev = rev2_
	g_mem_cached_rev_contents_lines = rev2_contents_lines
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

cmdtable = {
    # cmd name        function call
    'vimflipbook|vfb|fb': (hg_extension_main,
        # See mercurial/fancyopts.py for all of the command flag options.
        [],
        'FILE')
		}

testedwith = '3.0.1'

if __name__ == '__main__':

	standalone_main()

