#!/usr/bin/env python

import sys, re, subprocess, time, os, tempfile, shutil
from misc import *

class RevInfo(object):

	pass

def put_revision_info_into_env(revinfos_):
	put_revision_list_into_env(revinfos_)
	put_rev2loglinenum_map_into_env(revinfos_)

def put_rev2loglinenum_map_into_env(revinfos_):
	rev2loglinenum = {}
	linenum = 0
	for revinfo in revinfos_:
		rev2loglinenum[revinfo.rev] = linenum
		linenum += len(revinfo.lines_for_gui)
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
function! HgFlipbookSwitchRevision(next_or_prev) 
	let $HG_FLIPBOOK_LINENUM = line('.')
	let output = system($HG_FLIPBOOK_SCRIPT . ' --from-vim ' . a:next_or_prev)
	let output_splits = split(output, '|')
	let new_rev = output_splits[0]
	let new_filename = output_splits[1]
	let new_linenum = output_splits[2]
	let $HG_FLIPBOOK_CUR_REV = new_rev
	1 wincmd w
	execute 'edit'
	2 wincmd w
	execute 'edit' new_filename
	call cursor(new_linenum, col('.'))
endfunction

map <C-p> : call HgFlipbookSwitchRevision('prev')<CR>
map <C-n> : call HgFlipbookSwitchRevision('next')<CR>
'''
	filename = os.path.join(os.environ['HG_FLIPBOOK_TMPDIR'], 'vim-functions')
	with open(filename, 'w') as fout:
		fout.write(contents)
		return filename

def get_rev_filename(rev_):
	return os.path.join(os.environ['HG_FLIPBOOK_TMPDIR'], 'revision-%s' % rev_)

def write_rev_to_file(rev_):
	filename = get_rev_filename(rev_)
	with open(filename, 'w') as fout:
		args = ['hg', 'cat', '-r', rev_, os.environ['HG_FLIPBOOK_FILENAME']]
		hg_cat_output = subprocess.check_call(args, stdout=fout)
		return filename

def get_log_filename():
	return os.path.join(os.environ['HG_FLIPBOOK_TMPDIR'], 'log')

def write_virgin_log_file(revinfos_):
	filename = get_log_filename()
	with open(filename, 'w') as fout:
		for revinfo in revinfos_:
			for line in revinfo.lines_for_gui:
				print >> fout, '    %s    ' % line

def highlight_rev_in_log_file(rev_):
	rev2loglinenum = get_rev2loglinenum_from_env()
	linenum_to_highlight = rev2loglinenum[rev_]
	log_filename = get_log_filename()
	temp_fd, temp_filename = tempfile.mkstemp('logfile', dir=os.environ['HG_FLIPBOOK_TMPDIR'])
	with os.fdopen(temp_fd, 'w') as tmpfile_fout:
		with open(log_filename) as fin:
			for linei, line in enumerate(fin):
				line = line.rstrip('\r\n')
				if linei == linenum_to_highlight:
					left_spacer = '-->'; right_spacer = '<--'
				else:
					left_spacer = right_spacer = '   '
				print >> tmpfile_fout, left_spacer + line[3:-3] + right_spacer
	shutil.move(temp_filename, log_filename)

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
			cur_revinfo.lines_for_gui = [re.sub(delim, '', line)]
		else:
			if cur_revinfo is not None:
				cur_revinfo.lines_for_gui.append(line)

	if not revinfos:
		sys.exit('Found no revisions.')

	put_revision_info_into_env(revinfos)
	os.environ['HG_FLIPBOOK_FILENAME'] = filename_
	os.environ['HG_FLIPBOOK_SCRIPT'] = os.path.abspath(sys.argv[0])
	os.environ['HG_FLIPBOOK_TMPDIR'] = tempfile.mkdtemp('-hg-flipbook')
	init_rev = revinfos[0].rev
	os.environ['HG_FLIPBOOK_CUR_REV'] = init_rev
	write_virgin_log_file(revinfos)
	highlight_rev_in_log_file(init_rev)
	os.execvp('vim', ['vim', '-c', 'source '+create_vim_function_file(), '-c', 'resize 10', '-c', '2 wincmd w', 
			'-o', get_log_filename(), write_rev_to_file(init_rev)])

def get_filename_of_rev_creating_if_necessary(rev_):
	filename = get_rev_filename(rev_)
	if not os.path.exists(filename):
		filename_according_to_write = write_rev_to_file(rev_)
		assert filename_according_to_write == filename
	return filename

def get_upcoming_rev(next_aot_prev_):
	cur_rev = os.environ['HG_FLIPBOOK_CUR_REV']
	revs = get_revision_list_from_env()
	upcoming_rev_idx = revs.index(cur_rev) + (1 if next_aot_prev_ else -1)
	upcoming_rev_idx = rein_in(upcoming_rev_idx, 0, len(revs)-1)
	return revs[upcoming_rev_idx]

def from_vim_main(next_or_prev_):
	sys.stderr = open(os.path.join(os.environ['HG_FLIPBOOK_TMPDIR'], 'stderr'), 'w')
	if next_or_prev_ not in ('next', 'prev'):
		raise Exception("Expected 'next' or 'prev' as a command-line argument.")
	next_aot_prev = (next_or_prev_ == 'next')
	upcoming_rev = get_upcoming_rev(next_aot_prev)
	upcoming_rev_filename = get_filename_of_rev_creating_if_necessary(upcoming_rev)
	highlight_rev_in_log_file(upcoming_rev)
	print '%s|%s|%d' % (upcoming_rev, upcoming_rev_filename, get_new_linenum_from_env(upcoming_rev))

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

def get_new_linenum_from_env(upcoming_rev_):
	filename = os.environ['HG_FLIPBOOK_FILENAME']
	orig_linenum = int(os.environ['HG_FLIPBOOK_LINENUM'])
	rev1 = os.environ['HG_FLIPBOOK_CUR_REV']
	hunks = get_diff_hunks(filename, rev1, upcoming_rev_)
	return get_new_linenum(hunks, orig_linenum)

if __name__ == '__main__':

	if len(sys.argv) == 2:
		top_level_main(sys.argv[1])
	elif len(sys.argv) == 3 and sys.argv[1] == '--from-vim':
		from_vim_main(sys.argv[2])
	else:
		sys.exit("Don't understand arguments.")

