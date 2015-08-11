#!/usr/bin/env python

import sys, re, time, os
from misc import *

class RevInfo(object):

	pass

def put_revision_info_into_env(revinfos_):
	put_revision_list_into_env(revinfos_)
	put_rev2loglinenum_map_into_env(revinfos_)

# Line numbers in here are 0-based. 
def put_rev2loglinenum_map_into_env(revinfos_):
	rev2loglinenum = {}
	linenum = 0
	for revinfo in revinfos_:
		rev2loglinenum[revinfo.rev] = linenum
		linenum += len(revinfo.log_lines)
	os.environ['HG_FLIPBOOK_REV2LOGLINENUM'] = repr(rev2loglinenum)

def get_rev2loglinenum_from_env():
	return eval(os.environ['HG_FLIPBOOK_REV2LOGLINENUM'])

def put_revision_list_into_env(revinfos_):
	revs = []
	for revinfo in revinfos_:
		revs.append(revinfo.rev)
	os.environ['HG_FLIPBOOK_REVS'] = repr(revs)

def get_revision_list_from_env():
	return eval(os.environ['HG_FLIPBOOK_REVS'])

def create_vim_function_file():
	contents = r'''
function! HgFlipbookSwitchRevision(next_or_prev, n) 
	1 wincmd w
	let log_linenum = line('.')
	2 wincmd w
	let target_linenum = line('.')
	let output = system($HG_FLIPBOOK_SCRIPT . ' --from-vim ' . log_linenum . ' ' . target_linenum . ' ' . a:next_or_prev . ' ' . a:n)
	let output_splits = split(output, '|')
	let new_rev = output_splits[0]
	let new_filename = output_splits[1]
	let new_linenum = output_splits[2]
	let new_log_linenum = output_splits[3]
	let $HG_FLIPBOOK_CUR_REV = new_rev
	1 wincmd w
	execute 'edit'
	call cursor(new_log_linenum, col('.'))
	2 wincmd w
	execute 'edit' new_filename
	call cursor(new_linenum, col('.'))
endfunction

map <C-k> : call HgFlipbookSwitchRevision('prev', 1)  <CR>
map <C-j> : call HgFlipbookSwitchRevision('next', 1)  <CR>
map <C-p> : call HgFlipbookSwitchRevision('prev', 20) <CR>
map <C-n> : call HgFlipbookSwitchRevision('next', 20) <CR>
'''
	filename = os.path.join(os.environ['HG_FLIPBOOK_TMPDIR'], 'vim-functions')
	with open(filename, 'w') as fout:
		fout.write(contents)
		return filename

def get_rev_filename(rev_):
	return os.path.join(os.environ['HG_FLIPBOOK_TMPDIR'], 'revision-%s' % rev_)

def write_rev_to_file(rev_):
	import subprocess # Importing only when necessary.  For performance.
	filename = get_rev_filename(rev_)
	with open(filename, 'w') as fout:
		args = ['hg', 'cat', '-r', rev_, os.environ['HG_FLIPBOOK_FILENAME']]
		hg_cat_output = subprocess.check_call(args, stdout=fout)
		return filename

def get_log_filename():
	return os.path.join(os.environ['HG_FLIPBOOK_TMPDIR'], 'log')

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
def highlight_rev_in_log_file(rev_, rev2loglinenum_):
	linenum_to_highlight = rev2loglinenum_[rev_]
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

def top_level_main(filename_):
	delim = '___64576e96-ce85-4778-ab02-496fb264b41f___'
	args = ['hg', 'log', '--graph', '--template', 
			'%(delim)s{rev}%(delim)s:{node|short} {date|shortdate} {author|user} {desc|firstline}' % {'delim': delim}, filename_]
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

	put_revision_info_into_env(revinfos)
	os.environ['HG_FLIPBOOK_FILENAME'] = filename_
	os.environ['HG_FLIPBOOK_SCRIPT'] = os.path.abspath(sys.argv[0])
	os.environ['HG_FLIPBOOK_TMPDIR'] = tempfile.mkdtemp('-hg-flipbook')
	init_rev = revinfos[0].rev
	write_virgin_log_file(revinfos)
	highlighted_log_linenum = highlight_rev_in_log_file(init_rev, get_rev2loglinenum_from_env())
	os.execvp('vim', ['vim', '-c', 'source '+create_vim_function_file(), 
			'-c', 'set readonly', '-c', 'resize 10', 
			'-c', 'call cursor(%d,1)' % highlighted_log_linenum, 
			'-c', '2 wincmd w', '-c', 'set readonly', '-o', get_log_filename(), write_rev_to_file(init_rev)])

def get_filename_of_rev_creating_if_necessary(rev_):
	filename = get_rev_filename(rev_)
	if not os.path.exists(filename):
		filename_according_to_write = write_rev_to_file(rev_)
		assert filename_according_to_write == filename
	return filename

# arg orig_log_linenum_ - 1-based.
# arg rev2loglinenum_ - values are 0-based.
def get_cur_rev(orig_log_linenum_, rev2loglinenum_):
	revs = get_revision_list_from_env()
	for rev in revs:
		log_linenum = rev2loglinenum_[rev]
		if log_linenum > orig_log_linenum_-1:
			break
		r = rev
	return r

def get_upcoming_rev(cur_rev_, rev_offset_, rev2loglinenum_):
	revs = get_revision_list_from_env()
	upcoming_rev_idx = revs.index(cur_rev_) + rev_offset_
	upcoming_rev_idx = rein_in(upcoming_rev_idx, 0, len(revs)-1)
	return revs[upcoming_rev_idx]

def from_vim_main(orig_log_linenum_, orig_target_linenum_, next_or_prev_, n_):
	sys.stderr = open(os.path.join(os.environ['HG_FLIPBOOK_TMPDIR'], 'stderr'), 'w')
	if next_or_prev_ not in ('next', 'prev'):
		raise Exception("Expected 'next' or 'prev' as a command-line argument.")
	next_aot_prev = (next_or_prev_ == 'next')
	rev2loglinenum = get_rev2loglinenum_from_env()
	cur_rev = get_cur_rev(orig_log_linenum_, rev2loglinenum)
	rev_offset = n_*(1 if next_aot_prev else -1)
	upcoming_rev = get_upcoming_rev(cur_rev, rev_offset, rev2loglinenum)
	upcoming_rev_filename = get_filename_of_rev_creating_if_necessary(upcoming_rev)
	highlighted_log_linenum = highlight_rev_in_log_file(upcoming_rev, rev2loglinenum)
	upcoming_linenum = get_new_linenum(orig_target_linenum_, cur_rev, upcoming_rev)
	print '%s|%s|%d|%d' % (upcoming_rev, upcoming_rev_filename, upcoming_linenum, highlighted_log_linenum)

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
	return os.path.join(os.environ['HG_FLIPBOOK_TMPDIR'], 'hunks-%s-to-%s' % (rev1_, rev2_))

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
	import subprocess # Importing only when necessary.  For performance.
	filename = os.environ['HG_FLIPBOOK_FILENAME']
	args = ['hg', 'diff', '-U', '0', '-r', '%s:%s' % (rev1_, rev2_), filename]
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
		import subprocess, tempfile # Importing only when necessary.  For performance.
		top_level_main(sys.argv[1])
	elif len(sys.argv) == 6 and sys.argv[1] == '--from-vim':
		from_vim_main(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], int(sys.argv[5]))
	else:
		sys.exit("Don't understand arguments.")

