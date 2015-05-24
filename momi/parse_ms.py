from __future__ import division
import bisect
import networkx as nx

from demography import Demography
from size_history import ConstantHistory, ExponentialHistory, PiecewiseHistory
from util import default_ms_path

from autograd.numpy import isnan, exp,min

import random
import subprocess
import itertools
from collections import Counter
from cStringIO import StringIO

def make_demography(ms_cmd, *args, **kwargs):
    '''
    Returns a demography from partial ms command line

    See examples/example_sfs.py for more details
    '''
    return Demography(_to_nx(ms_cmd, *args, **kwargs))

def sfs_list_from_ms(ms_file, n_at_leaves):
    '''
    ms_file = file object containing output from ms
    n_at_leaves[i] = n sampled in leaf deme i

    Returns a list of empirical SFS's, one for each ms simulation
    '''
    lines = ms_file.readlines()

    def f(x):
        if x == "//":
            f.i += 1
        return f.i
    f.i = 0
    runs = itertools.groupby((line.strip() for line in lines), f)
    next(runs)

    pops_by_lin = []
    for pop in range(len(n_at_leaves)):
        for i in range(n_at_leaves[pop]):
            pops_by_lin.append(pop)

    return [_sfs_from_1_ms_sim(list(lines), len(n_at_leaves), pops_by_lin)
            for i,lines in runs]

def simulate_ms(demo, num_sims, theta, ms_path=None, seeds=None, additional_ms_params=""):
    '''
    Given a demography, simulate from it using ms

    Returns a file-like object with the ms output
    '''
    if ms_path is None:
        ms_path = default_ms_path()

    if any([(x in additional_ms_params) for x in "-t","-T","seed"]):
        raise IOError("additional_ms_params should not contain -t,-T,-seed,-seeds")

    lins_per_pop = [demo.n_lineages(l) for l in sorted(demo.leaves)]
    n = sum(lins_per_pop)

    ms_args = demo.ms_cmd
    if additional_ms_params:
        ms_args = "%s %s" % (ms_args, additional_ms_params)

    if seeds is None:
        seeds = [random.randint(0,999999999) for _ in range(3)]
    seeds = " ".join([str(s) for s in seeds])
    ms_args = "%s -seed %s" % (ms_args, seeds)

    assert ms_args.startswith("-I ")
    ms_args = "-t %f %s" % (theta, ms_args)
    ms_args = "%d %d %s" % (n, num_sims, ms_args)

    try:
        lines = subprocess.check_output([ms_path] + ms_args.split(),
                                        stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError, e:
        ## ms gives really weird error codes, so ignore them
        lines = e.output

    return StringIO(lines)

'''
Helper functions for constructing demography from ms command line.
'''

def _to_nx(ms_cmd, *args, **kwargs):
    '''
    Use demography.make_demography instead of calling this function directly.
    '''
    parser = MsCmdParser(*args, **kwargs)
    unsorted_list = get_cmd_list(ms_cmd)

    cmd_list = parser.sort_cmd_add_pops(unsorted_list)
    for event in cmd_list:
        parser.add_event(*event)
    return parser.to_nx()

def get_cmd_list(ms_cmd):
    cmd_list = []
    for arg in ms_cmd.split():
        if arg[0] == '-' and arg[1].isalpha():
            curr_args = [arg[1:]]
            cmd_list.append(curr_args)
        else:
            curr_args.append(arg)       
    assert cmd_list[0][0] == 'I'
    return cmd_list

class MsCmdParser(object):
    def __init__(self, *args, **kwargs):
        self.params_dict = dict(kwargs)
        for i,x in enumerate(args):
            self.params_dict[str(i)] = x

        self.events,self.edges,self.nodes = [],[],{}
        # roots is the set of nodes currently at the root of the graph
        self.roots = {}
        self.prev_time = 0.0
        self.cmd_list = []

    def add_event(self, event_flag, *args):
        args = getattr(self, '_' + event_flag)(*args)
        t = self.get_time(event_flag, *args)
        assert t >= self.prev_time
        self.prev_time = t
        self.cmd_list.append("-%s %s" % (event_flag, " ".join(map(str,args))))

    def sort_cmd_add_pops(self, unsorted_cmd):
        '''Sort the cmd and store the ordering of the populations'''
        n_leaf_pop = self.get_param(unsorted_cmd[0][1], int)

        sorted_cmd,times = [],[]
        sorted_pops = ['#'+str(i) for i in range(1,n_leaf_pop+1)]
        pop_times = [0.0]*n_leaf_pop
        for cmd in unsorted_cmd:
            t = self.get_time(cmd[0], *cmd[1:])
            idx = bisect.bisect_right(times, t)
            times.insert(idx, t)
            sorted_cmd.insert(idx, cmd)

            if cmd[0] == 'es':
                idx = bisect.bisect_right(pop_times, t)
                pop_times.insert(idx, t)
                sorted_pops.insert(idx, '#'+str(len(sorted_pops)+1))
        assert sorted_cmd[0][0] == 'I'

        self.pop_by_order = {x : i for i,x in enumerate(sorted_pops,1)}
        return sorted_cmd

    def _es(self, t,i,p):
        t,p = map(self.get_param, (t,p))
        i = self.get_pop(i)

        child = self.roots[i]
        self.set_sizes(self.nodes[child], t)

        parents = ((child,), len(self.roots)+1)
        assert all([par not in self.nodes for par in parents])

        self.nodes[child]['splitprobs'] = {par : prob for par,prob in zip(parents, [p,1-p])}

        prev = self.nodes[child]['sizes'][-1]
        self.nodes[parents[0]] = {'sizes':[{'t':t,'N':prev['N_top'], 'alpha':prev['alpha']}]}
        self.nodes[parents[1]] = {'sizes':[{'t':t,'N':1.0, 'alpha':None}]}

        new_edges = tuple([(par, child) for par in parents])
        self.events.append( new_edges )
        self.edges += list(new_edges)

        self.roots[i] = parents[0]
        self.roots[len(self.roots)+1] = parents[1]
        
        return t,i,p

    def _ej(self, t,i,j):
        t = self.get_param(t)
        i,j = map(self.get_pop, [i,j])

        for k in i,j:
            # sets the TruncatedSizeHistory, and N_top and alpha for all epochs
            self.set_sizes(self.nodes[self.roots[k]], t)

        new_pop = (self.roots[i], self.roots[j])
        self.events.append( ((new_pop,self.roots[i]),
                        (new_pop,self.roots[j]))  )

        assert new_pop not in self.nodes
        prev = self.nodes[self.roots[j]]['sizes'][-1]
        self.nodes[new_pop] = {'sizes':[{'t':t,'N':prev['N_top'], 'alpha':prev['alpha']}]}

        self.edges += [(new_pop, self.roots[i]), (new_pop, self.roots[j])]

        self.roots[j] = new_pop
        #del self.roots[i]
        self.roots[i] = None

        return t,i,j

    def _en(self, t,i,N):
        t,N = map(self.get_param, [t,N])
        i = self.get_pop(i)
        self.nodes[self.roots[i]]['sizes'].append({'t':t,'N':N,'alpha':None})
        return t,i,N

    def _eN(self, t, N):
        assert self.roots
        for i in self.roots:
             if self.roots[i] is not None:
                 self._en(t, i, N)
        return map(self.get_param, (t,N))

    def _eg(self, t,i,alpha):
        if self.get_param(alpha) == 0.0 and alpha[0] != "$":
            alpha = None
        else:
            alpha = self.get_param(alpha)
        t,i = self.get_param(t), self.get_pop(i)
        self.nodes[self.roots[i]]['sizes'].append({'t':t,'alpha':alpha})

        if alpha is None:
            alpha=0.0
        return t,i,alpha

    def _eG(self, t,alpha):
        assert self.roots
        for i in self.roots:
            if self.roots[i] is not None:
                self._eg(t,i,alpha)
        return map(self.get_param, (t,alpha))

    def _n(self, i,N):
        assert self.roots
        if self.events:
            raise IOError(("-n should be called before any demographic changes", kwargs['cmd']))
        assert not self.edges and len(self.nodes) == len(self.roots)

        i,N = self.get_pop(i), self.get_param(N)
        pop = self.roots[i]
        assert len(self.nodes[pop]['sizes']) == 1
        self.nodes[pop]['sizes'][0]['N'] = N

        return i,N

    def _g(self, i,alpha):
        assert self.roots
        if self.events:
            raise IOError(("-g,-G should be called before any demographic changes", kwargs['cmd']))
        assert not self.edges and len(self.nodes) == len(self.roots)
        i = self.get_pop(i)
        pop = self.roots[i]
        assert len(self.nodes[pop]['sizes']) == 1
        if self.get_param(alpha) == 0.0 and alpha[0] != "$":
            alpha = None
        else:
            alpha = self.get_param(alpha)
        self.nodes[pop]['sizes'][0]['alpha'] = alpha
        
        if alpha is None:
            alpha=0.0
        return i,alpha

    def _G(self, rate):
        assert self.roots
        for i in self.roots:
            if self.roots[i] is not None:
                self._g(i, rate)
        return self.get_param(rate),

    def _a(self, i, t):
        ## flag designates leaf population i is archaic, starting at time t
        assert self.roots
        if self.events:
            raise IOError(("-a should be called before any demographic changes", kwargs['cmd']))
        assert not self.edges and len(self.nodes) == len(self.roots)

        i,t = self.get_pop(i), self.get_param(t)
        pop = self.roots[i]
        assert len(self.nodes[pop]['sizes']) == 1
        self.nodes[pop]['sizes'][0]['t'] = t

        return i,t

    def _I(self, npop, *lins_per_pop):
        # -I should be called first, so everything should be empty
        assert all([not x for x in self.roots,self.events,self.edges,self.nodes])
        
        npop = self.get_param(npop, int)
        lins_per_pop = map(lambda x: self.get_param(x,int),
                           lins_per_pop)

        if len(lins_per_pop) != npop:
            raise IOError("Bad args for -I. Note continuous migration is not implemented.")

        for i in range(1,npop+1):
            self.nodes[i] = {'sizes':[{'t':0.0,'N':1.0,'alpha':None}],'lineages':lins_per_pop[i-1]}
            self.roots[i] = i
        return [npop] + lins_per_pop

    def get_param(self, var, vartype=float):
        if not isinstance(var,str):
            return var
        if var[0] == "$":
            ret = self.params_dict[var[1:]]
        else:
            ret = vartype(var)
        if vartype==float and isnan(ret):
            raise Exception("nan in params %s" % (str(self.params_dict)))
        return ret

    def get_time(self, event_type, *args):
        if event_type[0] == 'e':
            return self.get_param(args[0])
        return 0.0

    def get_pop(self, pop):
        ret = self.get_param(pop, str)
        if not isinstance(ret, str):
            return ret
        if ret[0] == '#':
            return self.pop_by_order[pop]
        return int(ret)

    def to_nx(self):
        assert self.nodes
        self.roots = [r for _,r in self.roots.iteritems() if r is not None]

        if len(self.roots) != 1:
            raise IOError("Must have a single root population")

        node, = self.roots
        self.set_sizes(self.nodes[node], float('inf'))

        cmd = " ".join(self.cmd_list)
        ret = nx.DiGraph(self.edges, cmd=cmd, events=self.events)
        for v in self.nodes:
            ret.add_node(v, **(self.nodes[v]))
        return ret

    def set_sizes(self, node_data, end_time):
        # add 'model_func' to node_data, add information to node_data['sizes']
        sizes = node_data['sizes']
        # add a dummy epoch with the end time
        sizes.append({'t': end_time})

        # do some processing
        N, alpha = sizes[0]['N'], sizes[0]['alpha']
        pieces = []
        for i in range(len(sizes) - 1):
            sizes[i]['tau'] = tau = (sizes[i+1]['t'] - sizes[i]['t'])

            if 'N' not in sizes[i]:
                sizes[i]['N'] = N
            if 'alpha' not in sizes[i]:
                sizes[i]['alpha'] = alpha
            alpha = sizes[i]['alpha']
            N = sizes[i]['N']

            if alpha is not None:
                pieces.append(ExponentialHistory(tau=tau,growth_rate=alpha,N_bottom=N))
                N = pieces[-1].N_top
            else:
                pieces.append(ConstantHistory(tau=tau, N=N))

            sizes[i]['N_top'] = N

            if not all([sizes[i][x] >= 0.0 for x in 'tau','N','N_top']):
                raise IOError("Negative time or population size. (Were events specified in correct order?")
        sizes.pop() # remove the final dummy epoch

        assert len(pieces) > 0
        if len(pieces) == 0:
            node_data['model'] = pieces[0]
        else:
            node_data['model'] = PiecewiseHistory(pieces)


'''
Helper functions for simulating SFS with ms.
'''

def _sfs_from_1_ms_sim(lines, num_pops, pops_by_lin):
    currCounts = Counter()
    n = len(pops_by_lin)

    assert lines[0] == "//"
    nss = int(lines[1].split(":")[1])
    if nss == 0:
        return currCounts
    # remove header
    lines = lines[3:]
    # remove trailing line if necessary
    if len(lines) == n+1:
        assert lines[n] == ''
        lines = lines[:-1]
    # number of lines == number of haplotypes
    assert len(lines) == n
    # columns are snps
    for column in range(len(lines[0])):
        dd = [0] * num_pops
        for i, line in enumerate(lines):
            dd[pops_by_lin[i]] += int(line[column])
        assert sum(dd) > 0
        currCounts[tuple(dd)] += 1
    return currCounts