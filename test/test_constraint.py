#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Nov  2 22:38:48 2021

@author: fabian
"""

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from xarray.testing import assert_equal

import linopy
from linopy import Model


def test_constraint_repr():
    m = Model()

    x = m.add_variables()
    c = m.add_constraints(x, ">=", 0)
    c.__repr__()
    c._repr_html_()


def test_constraints_repr():
    m = Model()
    m.constraints.__repr__()
    x = m.add_variables()
    m.add_constraints(x, ">=", 0)
    m.constraints.__repr__()


def test_constraint_accessor():
    m = Model()
    x = m.add_variables()
    c = m.add_constraints(x, ">=", 0)
    assert c.get_rhs().item() == 0
    assert c.get_vars().item() == 0
    assert c.get_coeffs().item() == 1
    assert c.get_sign().item() == ">="


def test_constraints_accessor():
    m = Model()

    lower = xr.DataArray(np.zeros((10, 10)), coords=[range(10), range(10)])
    upper = xr.DataArray(np.ones((10, 10)), coords=[range(10), range(10)])
    x = m.add_variables(lower, upper)
    y = m.add_variables()
    m.add_constraints(1 * x + 10 * y, "=", 0)
    assert m.constraints["con0"].shape == (10, 10)
    assert isinstance(m.constraints[["con0"]], linopy.constraints.Constraints)
    assert isinstance(m.constraints.inequalities, linopy.constraints.Constraints)
    assert isinstance(m.constraints.equalities, linopy.constraints.Constraints)


def test_constraint_getter_without_model():

    data = xr.DataArray(range(10)).rename("con")
    c = linopy.constraints.Constraint(data)

    with pytest.raises(AttributeError):
        c.get_coeffs()
    with pytest.raises(AttributeError):
        c.get_vars()
    with pytest.raises(AttributeError):
        c.get_sign()
    with pytest.raises(AttributeError):
        c.get_rhs()


def test_constraint_matrix():
    m = Model()
    x = m.add_variables(coords=[range(10)])
    y = m.add_variables()
    m.add_constraints(x, "=", 0)
    A = m.constraints.to_matrix()
    assert A.shape == (10, 11)


def test_constraint_matrix_masked_variables():
    """
    Test constraint matrix with missing variables.

    In this case the variables that are used in the constraints are missing.
    The matrix shoud not be built for constraints which have variables which
    are missing.
    """
    # now with missing variables
    m = Model()
    mask = pd.Series([False] * 5 + [True] * 5)
    x = m.add_variables(coords=[range(10)], mask=mask)
    m.add_variables()
    m.add_constraints(x, "=", 0)
    A = m.constraints.to_matrix(filter_missings=True)
    assert A.shape == (5, 6)
    assert A.shape == (m.ncons, m.nvars)

    A = m.constraints.to_matrix(filter_missings=False)
    assert A.shape == (m._cCounter, m._xCounter)


def test_constraint_matrix_masked_constraints():
    """
    Test constraint matrix with missing constraints.
    """
    # now with missing variables
    m = Model()
    mask = pd.Series([False] * 5 + [True] * 5)
    x = m.add_variables(coords=[range(10)])
    m.add_variables()
    m.add_constraints(x, "=", 0, mask=mask)
    A = m.constraints.to_matrix(filter_missings=True)
    assert A.shape == (5, 11)
    assert A.shape == (m.ncons, m.nvars)

    A = m.constraints.to_matrix(filter_missings=False)
    assert A.shape == (m._cCounter, m._xCounter)


def test_constraint_matrix_masked_constraints_and_variables():
    """
    Test constraint matrix with missing constraints.
    """
    # now with missing variables
    m = Model()
    mask = pd.Series([False] * 5 + [True] * 5)
    x = m.add_variables(coords=[range(10)], mask=mask)
    m.add_variables()
    m.add_constraints(x, "=", 0, mask=mask)
    A = m.constraints.to_matrix(filter_missings=True)
    assert A.shape == (5, 6)
    assert A.shape == (m.ncons, m.nvars)

    A = m.constraints.to_matrix(filter_missings=False)
    assert A.shape == (m._cCounter, m._xCounter)
