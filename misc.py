
import sys, os

def rein_in(x_, min_, max_):
	if x_ < min_:
		return min_
	elif x_ > max_:
		return max_
	else:
		return x_

def printerr(*args):
	sys.stderr.write(' '.join((str(x) for x in args)) + os.linesep)

