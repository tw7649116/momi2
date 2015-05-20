from __future__ import division
import numpy
import scipy.misc
import operator
import math
import gmpy2
from gmpy2 import mpz, mpfr
from util import memoize_instance

math_mod = gmpy2

gmpy2.get_context().precision=100

'''
Formulas from Hua Chen 2012, Theoretical Population Biology
Note that for all formulas from that paper, N = diploid population size
'''

class SumProduct_Chen(object):
    ''' 
    compute sfs of data via Hua Chen's sum-product algorithm
    '''
    def __init__(self, demography):
        self.G = demography
        attach_Chen(self.G)
    
    def p(self):
        '''Return the likelihood for the data'''
        return self.joint_sfs(self.G.root)

    @memoize_instance
    def partial_likelihood_top(self, node, n_ancestral_top, n_derived_top):
        n_leaves = self.G.n_lineages_subtended_by[node]
        ret = 0.0
        for n_derived_bottom in range(n_derived_top, n_leaves + 1):
            for n_ancestral_bottom in range(n_leaves - n_derived_bottom + 1):
                n_bottom = n_derived_bottom + n_ancestral_bottom
                n_top = n_derived_top + n_ancestral_top

                if n_bottom < n_top or (n_derived_bottom > 0 and n_derived_top == 0):
                    continue

                p_bottom = self.partial_likelihood_bottom(node, n_ancestral_bottom, n_derived_bottom)
                if p_bottom == 0.0:
                    continue
                p_top = p_bottom * self.G.chen[node].g(n_bottom,n_top)

                if n_derived_bottom > 0:
                    p_top *= math.exp(log_urn_prob(
                            n_derived_top,
                            n_ancestral_top,
                            n_derived_bottom, 
                            n_ancestral_bottom))
                ret += p_top
        return ret

    @memoize_instance
    def partial_likelihood_bottom(self, node, n_ancestral, n_derived):
        '''Likelihood of data given alleles (state) at bottom of node.'''
        # Leaf nodes are "clamped"
        if self.G.is_leaf(node):
            if n_ancestral + n_derived == self.G.n_lineages_subtended_by[node] and n_derived == self.G.n_derived_subtended_by[node]:
                return 1.0
            else:
                return 0.0
        # Sum over allocation of lineages to left and right branch
        # Left branch gets between 1 and (total - 1) lineages
        ret = 0.0
        total_lineages = n_ancestral + n_derived

        left_node,right_node = self.G[node]

        n_leaves_l = self.G.n_lineages_subtended_by[left_node]
        n_leaves_r = self.G.n_lineages_subtended_by[right_node]
        for n_ancestral_l in range(n_ancestral + 1):
            n_ancestral_r = n_ancestral - n_ancestral_l
            # Sum over allocation of derived alleles to left, right branches
            for n_derived_l in range(n_derived + 1):
                n_derived_r = n_derived - n_derived_l
                n_left = n_ancestral_l + n_derived_l
                n_right = n_ancestral_r + n_derived_r
                if any([n_right == 0, n_right > n_leaves_r, n_left==0, n_left > n_leaves_l]):
                    continue
                p = math.exp(logbinom(n_ancestral, n_ancestral_l) + 
                         logbinom(n_derived, n_derived_l) - 
                         logbinom(total_lineages, n_ancestral_l + n_derived_l))
                assert p != 0.0
                for args in ((right_node, n_ancestral_r, n_derived_r), (left_node, n_ancestral_l, n_derived_l)):
                    p *= self.partial_likelihood_top(*args)
                ret += p
        return ret

    @memoize_instance
    def joint_sfs(self, node):
        n_leaves = self.G.n_lineages_subtended_by[node]
        ret = 0.0
        for n_bottom in range(1, n_leaves+1):
            for n_top in range(1, n_bottom+1):
                for n_derived in range(1, n_bottom - n_top + 1):
                    n_ancestral = n_bottom - n_derived

                    p_bottom = self.partial_likelihood_bottom(node, n_ancestral, n_derived)
                    ret += p_bottom * self.G.chen[node].ES_i(n_derived, n_bottom, n_top)

        if self.G.is_leaf(node):
            return ret

        # add on terms for mutation occurring below this node
        # if no derived leafs on right, add on term from the left
        c1, c2 = self.G[node]
        for child, other_child in ((c1, c2), (c2, c1)):
            if self.G.n_derived_subtended_by[child] == 0:
                ret += self.joint_sfs(other_child)
        return ret


def attach_Chen(tree):
    '''Attach Hua Chen equations to each node of tree.
    Does nothing if these formulas have already been added.'''
    if not hasattr(tree, "chen"):
        tree.chen = {}
        for node in tree:
            size_model = tree.node_data[node]['model']
            tree.chen[node] = SFS_Chen(size_model.N / 2.0, size_model.tau)

class SFS_Chen(object):
    def __init__(self, N_diploid, timeLen):
        self.timeLen = timeLen
        self.N_diploid = N_diploid
            
    @memoize_instance    
    def g(self, n, m):
        return g(n, m, self.N_diploid, self.timeLen)

    @memoize_instance
    def ET(self, i, n, m):
        return ET(i, n, m, self.N_diploid, self.timeLen)

    @memoize_instance    
    def ES_i(self, i, n, m):
        '''TPB equation 4'''
        assert n >= m
        ret = math.fsum([p_n_k(i, n, k) * k * self.ET(k, n, m) for k in range(m, n + 1)])
        return ret

def binom_exact(n, k):
    return scipy.misc.comb(n, k, True)

def prod(l):
    return reduce(operator.mul, l + [1])

def rising(n, k):
    return prod(range(n, n + k))


def falling(n, k):
    return prod(range(n - k + 1, n + 1))


def gcoef(k, n, m, N_diploid, tau):
    k, n, m, N_diploid = map(mpz, [k, n, m, N_diploid])
    tau = mpfr(tau)
    return (2*k - 1) * (-1)**(k - m) * rising(m, k-1) * falling(n, k) / math_mod.factorial(m) / math_mod.factorial(k - m) / rising(n, k) 


def g_sum(n, m, N_diploid, tau):
    if tau == float("inf"):
        if m == 1:
            return 1.0
        return 0.0
    tau = mpfr(tau)
    return float(sum([gcoef(k, n, m, N_diploid, tau) * math_mod.exp(-k * (k - 1) * tau / 4 / N_diploid) for k in range(m, n + 1)]))


g = g_sum

def log_g(n, m, N_diploid, tau):
    assert n >= m
    return float(math_mod.log(g(n, m, N_diploid, tau)))

def formula1(n, m, N_diploid, tau):
    def expC2(k):
        return math_mod.exp(-k * (k - 1) / 4 / N_diploid * tau)
    r = sum(gcoef(k, n, m, N_diploid, tau) * 
            ((expC2(m) - expC2(k)) / (k - m) / (k + m - 1) - (tau / 4 / N_diploid * expC2(m)))
            for k in range(m + 1, n + 1))
    q = 4 * N_diploid / g(n, m, N_diploid, tau)
    return float(r * q)


def formula3(j, n, m, N_diploid, tau):
    # Switch argument to j here to stay consistent with the paper.
    j, n, m, N_diploid = map(mpz, [j, n, m, N_diploid])
    tau = mpfr(tau)
    def expC2(kk):
        return math_mod.exp(-kk * (kk - 1) / 4 / N_diploid * tau)
    r = sum(gcoef(k, n, j, N_diploid, tau) * # was gcoef(k, n, j + 1, N_diploid, tau) * 
            sum(gcoef(ell, j, m, N_diploid, tau) * ( # was gcoef(ell, j - 1, m, N_diploid, tau) * (
                    (
                        expC2(j) * (tau / 4 / N_diploid - ((k - j) * (k + j - 1) + (ell - j)*(ell + j - 1)) / # tau / 4 / N_diploid was 1 in this
                             (k - j) / (k + j- 1) / (ell - j) / (ell + j - 1))
                    )
                    +
                    (
                        expC2(k) * (ell - j) * (ell + j - 1) / (k - j) / (k + j - 1) / (ell - k) / (ell + k - 1)
                    )
                    -
                    (
                        expC2(ell) * (k - j) * (k + j - 1) / (ell - k) / (ell + k - 1) / (ell - j) / (ell + j - 1)
                    )
                )
                for ell in range(m, j)
                )
            for k in range(j + 1, n + 1)
            )
    q = 4 * N_diploid / mpfr(g(n, m, N_diploid, tau))
    return float(q * r)


def formula2(n, m, N_diploid, tau):
    def expC2(k):
        return math_mod.exp(-k * (k - 1) / 4 / N_diploid * tau)
    r = sum(gcoef(k, n, m, N_diploid, tau) * 
            ((expC2(k) - expC2(n)) / (n - k) / (n + k - 1) - (tau / 4 / N_diploid * expC2(n)))
            for k in range(m, n))
    q = 4 * N_diploid / g(n, m, N_diploid, tau)
    return float(r * q)

def ET(i, n, m, N_diploid, tau):
    '''Starting with n lineages in a population of size N_diploid,
    expected time when there are i lineages conditional on there
    being m lineages at time tau in the past.'''
    if tau == float("inf"):
        if m != 1 or i == 1:
            return 0.0
        return 2 * N_diploid / float(nChoose2(i))
    if n == m:
        return tau * (i == n)
    if m == i:
        return formula1(n, m, N_diploid, tau)
    elif n == i:
        return formula2(n, m, N_diploid, tau)
    else:
        return formula3(i, n, m, N_diploid, tau)

def p_n_k(i, n, k):
    if k == 1:
        return int(i == n)
    else:
        return binom_exact(n - i - 1, k - 2) / binom_exact(n - 1, k - 1)

def nChoose2(n):
    return (n * (n-1)) / 2

def logfact(n):
    return math.lgamma(n + 1)

def logbinom(n, k):
    return logfact(n) - logfact(n - k) - logfact(k)

def log_urn_prob(n_parent_derived, n_parent_ancestral, n_child_derived, n_child_ancestral):
    n_parent = n_parent_derived + n_parent_ancestral
    n_child = n_child_derived + n_child_ancestral
    if n_child_derived >= n_parent_derived and n_parent_derived > 0 and n_child_ancestral >= n_parent_ancestral and n_parent_ancestral > 0:
        return logbinom(n_child_derived - 1, n_parent_derived - 1) + logbinom(n_child_ancestral - 1, n_parent_ancestral - 1) - logbinom(n_child-1, n_parent-1)
    elif n_child_derived == n_parent_derived == 0 or n_child_ancestral == n_parent_ancestral == 0:
        return 0.0
    else:
        return float("-inf")