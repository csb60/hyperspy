# -*- coding: utf-8 -*-
# Copyright © 2007 Francisco Javier de la Peña
#
# This file is part of EELSLab.
#
# EELSLab is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# EELSLab is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EELSLab; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  
# USA


import math, copy, os

import numpy as np
import scipy as sp
from scipy.interpolate import splev,splrep,splint
from numpy import log, exp
from scipy.signal import cspline1d_eval

from eelslab.defaults_parser import defaults
from eelslab.component import Component
from eelslab.microscope import microscope
from eelslab.effectiveangle import EffectiveAngle
from eelslab.edges_db import edges_dict
from eelslab import messages


# Global constants
# Fundamental constants
R = 13.6056923 #Rydberg of energy in eV
e = 1.602176487e-19 #electron charge in C
m0 = 9.10938215e-31 #electron rest mass in kg
a0 = 5.2917720859e-11 #Bohr radius in m
c = 2997.92458e8 #speed of light in m/s

# Relativistic correction factors
gamma = 1.0 + (e * microscope.E0) / (m0 * pow(c,2.0)) #dimensionless
T = microscope.E0 * (1.0 + gamma) / (2.0 * pow(gamma, 2.0)) #in eV

class Edge(Component):
    """ This class builds a single cross section edge.
    Currently it only supports cross sections from Gatan Digital Micrograph(c)
    P. Rez calculations, although hydrogenic cross sections will be available.
    To model the fine structure with a small number of parameters a spline
    is used to fit to the experimental data. 
    """

    def __init__(self, element_subshell, intensity=1.,delta=0.):
        # Check if the Peter Rez's Hartree Slater GOS distributed by Gatan 
        # are available. Otherwise exit
        if defaults.GOS_dir == 'None':
            messages.warning_exit(
            "The path to the GOS files could not be found.\n" \
            "Please define a valid GOS folder location in the configuration" \
            " file.")
        # Declare which are the "real" parameters
        Component.__init__(self, ['delta', 'intensity', 'fslist', 
        'effective_angle'])
        self.name = element_subshell
        # Set initial values
        self.__element, self.__subshell = element_subshell.split('_')
        self.dispersion = 0.1
        self.effective_angle.value = 0
        self.effective_angle.free = False
        self.fs_state = defaults.fs_state
        self.fs_emax = defaults.fs_emax
        self.fs_mode = "new_spline"
        self.fslist.ext_force_positive = False
        
        self.delta.value = delta
        self.delta.free = False
        self.delta.ext_force_positive = False
        self.delta.grad = self.grad_delta
        self.freedelta = False
        self.__previous_delta = delta
                                
        self.intensity.grad = self.grad_intensity
        self.intensity.value = intensity
        self.intensity.bmin = 0.
        self.intensity.bmax = None

        self.knots_factor = defaults.knots_factor

        # Set initial actions
        self.readgosfile()
        self.integrategos(self.delta.value)

        
    # Automatically fix the fine structure when the fine structure is disable.
    # This way we avoid a common source of problems when fitting
    # However the fine structure must be *manually* freed when we reactivate
    # the fine structure.
    def _get_fs_state(self):
            return self.__fs_state
    def _set_fs_state(self,arg):
        if arg is False:
            self.fslist.free = False
        self.__fs_state = arg
    fs_state = property(_get_fs_state,_set_fs_state)
    
    def _get_fs_emax(self):
        return self.__fs_state
    def _set_fs_emax(self,arg):
        self.__fs_emax = arg
        self.setfslist()
    fs_emax = property(_get_fs_emax,_set_fs_emax)

    def edge_position(self):
        return self.edgeenergy + self.delta.value
        
    def setfslist(self) :
        self.fslist._number_of_elements = \
        int(round(self.knots_factor*self.fs_emax / self.dispersion)) + 4
        self.fslist.bmin, self.fslist.bmax = None, None
        self.fslist.value=np.zeros(self.fslist._number_of_elements).tolist()
        self.calculate_knots()
        if self.fslist.map is not None:
            xy_shape = list(self.fslist.map.shape[:2])    
            self.fslist.map = np.zeros(xy_shape + 
            [self.fslist._number_of_elements,])
            self.fslist.std_map = np.zeros(xy_shape + 
            [self.fslist._number_of_elements,])
        
    def readgosfile(self): 
        element = self.__element
        # Convert to the "GATAN" nomenclature
        if self.__subshell == "K" :
            subshell = "K1"
        else:
            subshell = self.__subshell
        if edges_dict.has_key(element) is not True:
            message = "The given element " + element + \
            " is not in the database."
            messages.warning_exit(message)
        elif edges_dict[element]['subshells'].has_key(subshell) is not True :
            message =  "The given subshell " + subshell + \
            " is not in the database." + "\nThe available subshells are:\n" + \
            str(edges_dict[element]['subshells'].keys())
            messages.warning_exit(message)
            
        self.edgeenergy = \
        edges_dict[element]['subshells'][subshell]['onset_energy']
        self.__subshell_factor = \
        edges_dict[element]['subshells'][subshell]['factor']
        print "\nLoading Hartree-Slater cross section from the Gatan tables"
        print "Element: ", element
        print "Subshell: ", subshell
        print "Onset Energy = ", self.edgeenergy
        print "Convergence angle = ", microscope.alpha
        print "Collection angle = ", microscope.beta
        #Read file
        file = os.path.join(defaults.GOS_dir, 
        edges_dict[element]['subshells'][subshell]['filename'])
        f = open(file)
 
        #Tranfer the content of the file to a list
        GosList = f.read().replace('\r','').split()

        #Extract the parameters

        self.material = GosList[0]
        self.__info1_1 = float(GosList[2])
        self.__info1_2 = float(GosList[3])
        self.__info1_3 = float(GosList[4])
        self.__ncol    = int(GosList[5])
        self.__info2_1 = float(GosList[6])
        self.__info2_2 = float(GosList[7])
        self.__nrow    = int(GosList[8])
        self.__gos_array = np.array(GosList[9:]).reshape(self.__nrow, 
        self.__ncol).astype(np.float64)
        
        # Calculate the scale of the matrix
        self.energyaxis = self.__info2_1 * (exp(np.linspace(0, 
        self.__nrow-1,self.__nrow) * self.__info2_2 / self.__info2_1) - 1.0)
        
        self.__qaxis=(self.__info1_1 * (exp(np.linspace(1, self.__ncol, 
        self.__ncol) * self.__info1_2) - 1.0)) * 1.0e10
        self.__sqa0qaxis = (a0 * self.__qaxis)**2
        self.__logsqa0qaxis = log((a0 * self.__qaxis)**2)
        
    def integrategos(self,delta = 0):
        """
        Calculates the knots of the spline interpolation of the cross section 
        after integrating q. It calculates it for Ek in the range 
        (Ek-Ekrange,Ek+Ekrange) for optimizing the time of the fitting. 
        For a value outside of the range it returns the closer limit, 
        however this is not likely to happen in real data
        """	
        
        def getgosenergy(i):
            """
            Given the row number i (starting from 0) returns the corresponding 
            energy
            """	
            return self.__info2_1 * (math.exp(i * self.__info2_2 / \
            self.__info2_1) - 1.0)
        
        def emax(edgeenergy,i): return self.energyaxis[i] + edgeenergy
        qint = sp.zeros((self.__nrow))
        
        # Integration over q using splines
        if self.effective_angle.value == 0:
            self.effective_angle.value = \
            EffectiveAngle(microscope.E0, self.edgeenergy, 
            microscope.alpha, microscope.beta)
            self.__previous_effective_angle = self.effective_angle.value
        effective_angle = self.effective_angle.value
        for i in range(0,self.__nrow):
            qtck = splrep(self.__logsqa0qaxis, self.__gos_array[i, :], s=0)
            qa0sqmin = (emax(self.edgeenergy + self.delta.value, i)**2) / (
            4.0 * R * T) + (emax(self.edgeenergy + self.delta.value, 
            i)**3) / (8.0 * gamma ** 3.0 * R * T**2)
            qa0sqmax = qa0sqmin + 4.0 * gamma**2 * (T/R) * math.sin(
            effective_angle / 2.0)**2.0
            qmin = math.sqrt(qa0sqmin) / a0
            qmax=math.sqrt(qa0sqmax) / a0
            
            # Error messages for out of tabulated data
            if qmax > self.__qaxis[-1] :
                print "i=",i
                print "Maximum tabulated q reached!!"
                print "qa0sqmax=",qa0sqmax
                qa0sqmax = self.__sqa0qaxis[self.__ncol-1]
                print "qa0sqmax tabulated maximum", 
                self.__sqa0qaxis[self.__ncol-1]
                
            if qmin < self.__qaxis[0] :
                print "i=",i
                print "Minimum tabulated q reached!! Accuracy not garanteed"
                print "qa0sqmin",qa0sqmin
                qa0sqmin = self.__sqa0qaxis[0]
                print "qa0sqmin tabulated minimum", qa0sqmin
            
            # Writes the integrated values to the qint array.
            qint[i] = splint(math.log(qa0sqmin), math.log(qa0sqmax), qtck)
        self.__qint = qint        
        self.__goscoeff = splrep(self.energyaxis,qint,s=0)
        
        # Calculate extrapolation powerlaw extrapolation parameters
        E1 = self.energyaxis[-2] + self.edgeenergy + self.delta.value
        E2 = self.energyaxis[-1] + self.edgeenergy + self.delta.value
        factor = 4.0 * np.pi * a0 ** 2.0 * R**2.0 / E1 / T
        y1 = factor * splev((E1 - self.edgeenergy - self.delta.value), 
        self.__goscoeff) # in m**2/bin */
        factor = 4.0 * np.pi * a0 ** 2.0 * R ** 2.0 / E2 / T
        y2 = factor * splev((E2 - self.edgeenergy - self.delta.value), 
        self.__goscoeff) # in m**2/bin */
        self.r = math.log(y2 / y1) / math.log(E1 / E2)
        self.A = y1 / E1**-self.r
        
    def calculate_knots(self):    
        # Recompute the knots
        start = self.edgeenergy + self.delta.value
        stop = start + self.fs_emax
        self.__knots = np.r_[[start]*4,
        np.linspace(start, stop, self.fslist._number_of_elements)[2:-2], 
        [stop]*4]
        
    def function(self,E) :
        """ Calculates the number of counts in barns"""
        
        if self.delta.value != self.__previous_delta :
            self.__previous_delta = copy.copy(self.delta.value)
            self.integrategos(self.delta.value)
            self.calculate_knots()

        if self.__previous_effective_angle != self.effective_angle.value:
            self.integrategos()
            
        factor = 4.0 * np.pi * a0 ** 2.0 * R**2 / E / T #to convert to m**2/bin
        Emax = self.energyaxis[-1] + self.edgeenergy + \
        self.delta.value #maximum tabulated energy
        cts = np.zeros((len(E)))
        
        if self.fs_state is True:
            if self.__knots[-1] > Emax : Emax = self.__knots[-1]
            fine_structure_indices=np.logical_and(np.greater_equal(E, 
            self.edgeenergy+self.delta.value), 
            np.less(E, self.edgeenergy + self.delta.value + self.fs_emax))
            tabulated_indices = np.logical_and(np.greater_equal(E, 
            self.edgeenergy + self.delta.value + self.fs_emax), 
            np.less(E, Emax))
            if self.fs_mode == "new_spline" :
                cts = np.where(fine_structure_indices, 
                1E-25*splev(E,(self.__knots,self.fslist.value,3),0), cts)
            elif self.fs_mode == "spline" :
                cts = np.where(fine_structure_indices, 
                cspline1d_eval(self.fslist.value, 
                E, 
                dx = self.dispersion / self.knots_factor, 
                x0 = self.edgeenergy+self.delta.value), 
                cts)
            elif self.fs_mode == "spline_times_edge" :
                cts = np.where(fine_structure_indices, 
                factor*splev((E-self.edgeenergy-self.delta.value), 
                self.__goscoeff)*cspline1d_eval(self.fslist.value, 
                E,dx = self.dispersion / self.knots_factor, 
                x0 = self.edgeenergy+self.delta.value), 
                cts )
        else:
            tabulated_indices = np.logical_and(np.greater_equal(E, 
            self.edgeenergy + self.delta.value), np.less(E, Emax))            
        powerlaw_indices = np.greater_equal(E,Emax)  
        cts = np.where(tabulated_indices, 
        factor * splev((E-self.edgeenergy-self.delta.value), 
        self.__goscoeff),
         cts)
        
        # Convert to barns/dispersion.
        #Note: The R factor is introduced in order to give the same value
        # as DM, although it is not in the equations.
        cts = np.where(powerlaw_indices, self.A * E**-self.r, cts) 
        return (self.__subshell_factor * self.intensity.value * self.dispersion 
        * 1.0e28 / R) * cts       
    
    def grad_intensity(self,E) :
        
        if self.delta.value != self.__previous_delta :
            self.__previous_delta = copy.copy(self.delta.value)
            self.integrategos(self.delta.value)
            self.calculate_knots()
            
        factor = 4.0 * np.pi * a0 ** 2.0 * \
        (R ** 2.0) / (E * T) #to convert to m**2/bin
        Emax = self.energyaxis[-1] + self.edgeenergy + \
        self.delta.value #maximum tabulated energy
        cts = np.zeros((len(E)))
        
        if self.fs_state is True:
            if self.__knots[-1] > Emax : Emax = self.__knots[-1]
            fine_structure_indices=np.logical_and(np.greater_equal(E, 
            self.edgeenergy+self.delta.value), 
            np.less(E, self.edgeenergy + self.delta.value + self.fs_emax))
            tabulated_indices = np.logical_and(np.greater_equal(E, 
            self.edgeenergy + self.delta.value + self.fs_emax), 
            np.less(E, Emax))
            if self.fs_mode == "new_spline" :
                cts = np.where(fine_structure_indices, 
                1E-25*splev(E,(self.__knots,self.fslist.value,3),0), cts)
            elif self.fs_mode == "spline" :
                cts = np.where(fine_structure_indices, 
                cspline1d_eval(self.fslist.value, 
                E, 
                dx = self.dispersion / self.knots_factor, 
                x0 = self.edgeenergy+self.delta.value), 
                cts)
            elif self.fs_mode == "spline_times_edge" :
                cts = np.where(fine_structure_indices, 
                factor*splev((E-self.edgeenergy-self.delta.value), 
                self.__goscoeff)*cspline1d_eval(self.fslist.value, 
                E,dx = self.dispersion / self.knots_factor, 
                x0 = self.edgeenergy+self.delta.value), 
                cts )
        else:
            tabulated_indices = np.logical_and(np.greater_equal(E, 
            self.edgeenergy + self.delta.value), np.less(E, Emax))
        powerlaw_indices = np.greater_equal(E,Emax)  
        cts = np.where(tabulated_indices, 
        factor * splev((E-self.edgeenergy-self.delta.value), 
        self.__goscoeff),
         cts)
        
        # Convert to barns/dispersion.
        #Note: The R factor is introduced in order to give the same value
        # as DM, although it is not in the equations.
        cts = np.where(powerlaw_indices, self.A * pow(E,-self.r), cts)
        return ((1.0e28 *self.__subshell_factor * self.dispersion)/R)*cts        

    
    def grad_delta(self,E) :
        """ Calculates the number of counts in barns"""
        
        if self.delta.value != self.__previous_delta :
            self.__previous_delta = copy.copy(self.delta.value)
            self.integrategos(self.delta.value)
            self.calculate_knots()
        factor = 4.0 * np.pi * (a0**2.0) * (
        R**2.0) / (E * T) #to convert to m**2/bin
        Emax = self.energyaxis[-1] + self.edgeenergy + \
        self.delta.value #maximum tabulated energy
        cts = np.zeros((len(E)))
        
        if self.fs_state is True:
            if self.__knots[-1] > Emax : Emax = self.__knots[-1]
            fine_structure_indices=np.logical_and(np.greater_equal(E, 
            self.edgeenergy+self.delta.value), 
            np.less(E, self.edgeenergy + self.delta.value + self.fs_emax))
            tabulated_indices = np.logical_and(np.greater_equal(E, 
            self.edgeenergy + self.delta.value + self.fs_emax), 
            np.less(E, Emax))
            cts = 1E-25 * np.where(fine_structure_indices, 
            splev(E,(self.__knots,self.fslist.value,3),1), cts)
        else:
            tabulated_indices = np.logical_and(np.greater_equal(E, 
            self.edgeenergy + self.delta.value), np.less(E, Emax))
        
        powerlaw_indices = np.greater_equal(E,Emax)  
        cts = np.where(tabulated_indices, 
        factor * splev((E-self.edgeenergy-self.delta.value), 
        self.__goscoeff, 1),
         cts)
        
        # Convert to barns/dispersion.
        #Note: The R factor is introduced in order to give the same value
        # as DM, although it is not in the equations.
        cts = np.where(powerlaw_indices, -self.r * self.A *\
         (E**-self.r-1), cts)
        return - ((1.0e28 *self.__subshell_factor * self.intensity.value 
    * self.dispersion)/R) * cts         

    def fslist_to_txt(self,filename) :
        np.savetxt(filename + '.dat', self.fslist.value, fmt="%12.6G")
    def txt_to_fslist(self,filename) :
        fs = np.loadtxt(filename)
        self.calculate_knots()
        if len(fs) == len(self.__knots) :
            self.fslist.value = fs
        else :
            messages.warning_exit("The provided fine structure file "  
            "doesn't match the size of the current fine structure")