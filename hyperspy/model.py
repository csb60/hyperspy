# -*- coding: utf-8 -*-
# Copyright 2007-2011 The Hyperspy developers
#
# This file is part of  Hyperspy.
#
#  Hyperspy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
#  Hyperspy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with  Hyperspy.  If not, see <http://www.gnu.org/licenses/>.

import copy
import os
import tempfile

import numpy as np
import traits.api as t

from hyperspy.estimators import Estimators
from hyperspy.optimizers import Optimizers
from hyperspy import messages
import hyperspy.drawing.spectrum
from hyperspy.drawing.utils import on_figure_window_close
from hyperspy.misc import progressbar
from hyperspy.signals.eels import EELSSpectrum, Spectrum
from hyperspy.defaults_parser import preferences
from hyperspy.axes import generate_axis
from hyperspy.exceptions import WrongObjectError
from hyperspy.decorators import interactive_range_selector

class Model(list, Optimizers, Estimators):
    """Build and fit a model
    
    Parameters
    ----------
    spectrum : an Spectrum (or any Spectrum subclass) instance
    """
    
    _firstimetouch = True

    def __init__(self, spectrum):
        self.convolved = False
        self.auto_update_plot = False
        self.spectrum = spectrum
        self.axes_manager = self.spectrum.axes_manager
        self.axis = self.axes_manager._slicing_axes[0]
        self.axes_manager.connect(self.charge)
         
        self.free_parameters_boundaries = None
        # TODO: model cube should dissapear or at least be an option
        self.model_cube = np.zeros(self.spectrum.data.shape, 
                                   dtype = 'float')
        self.model_cube[:] = np.nan
        self.channel_switches=np.array([True] * len(self.axis.axis))
        self._low_loss = None

    @property
    def spectrum(self):
        return self._spectrum
        
    @spectrum.setter
    def spectrum(self, value):
        if isinstance(value, Spectrum):
            self._spectrum = value
        else:
            raise WrongObjectError(str(type(value)), 'Spectrum')
                    
    @property
    def low_loss(self):
        return self._low_loss
        
    @low_loss.setter
    def low_loss(self, value):
        if value is not None:
            self._low_loss = value
            self.set_convolution_axis()
            self.convolved = True
        else:
            self._low_loss = value
            self.convolution_axis = None
            self.convolved = False

        
    # Extend the list methods to call the _touch when the model is modified
    def append(self, object):
        object.create_arrays(self.axes_manager.navigation_shape)
        object.set_axes(self.axes_manager)
        list.append(self,object)
        self._touch()
    
    def insert(self, object):
        object.create_arrays(self.axes_manager.navigation_shape)
        object.set_axes(self.axes_manager)
        list.insert(self,object)
        self._touch()
   
    def extend(self, iterable):
        for object in iterable:
            object.create_arrays(self.axes_manager.navigation_shape)
            object.set_axes(self.axes_manager)
        list.extend(self,iterable)
        self._touch()
                
    def __delitem__(self, object):
        list.__delitem__(self,object)
        self._touch()
    
    def remove(self, object, touch = True):
        list.remove(self,object)
        if touch is True:
            self._touch() 

    def _touch(self):
        """Run model setup tasks
        
        This function is called everytime that we add or remove components
        from the model.
        """
        self.connect_parameters2update_plot()
        
    __touch = _touch
    
    def set_convolution_axis(self):
        """
        Creates an axis to use to generate the data of the model in the precise
        scale to obtain the correct axis and origin after convolution with the
        lowloss spectrum.
        """
        ll_axis = self.low_loss.axes_manager._slicing_axes[0]
        dimension = self.axis.size + ll_axis.size - 1
        step = self.axis.scale
        knot_position = ll_axis.size - ll_axis.value2index(0) - 1
        self.convolution_axis = generate_axis(self.axis.offset, step, 
        dimension, knot_position)
                
    def connect_parameters2update_plot(self):   
        for component in self:
            for parameter in component.parameters:
                if self.spectrum._plot is not None:
                    parameter.connect(self.update_plot)
                parameter.connection_active = False
    
    def disconnect_parameters2update_plot(self):
        for component in self:
            for parameter in component.parameters:
                parameter.disconnect(self.update_plot)
                parameter.connection_active = False
        self.set_auto_update_plot(False)
                            
    def set_auto_update_plot(self, tof):
        for component in self:
            for parameter in component.parameters:
                parameter.connection_active = tof
        self.auto_update_plot = tof

    def generate_data_from_model(self, out_of_range_to_nan = True):
        """Generate a SI with the current model
        
        The SI is stored in self.model_cube
        """
        pbar = progressbar.progressbar(
        maxval = (np.cumprod(self.axes_manager.navigation_shape)[-1]))
        i = 0
        for index in np.ndindex(tuple(self.axes_manager.navigation_shape)):
            self.axes_manager.set_not_slicing_indexes(index)
            self.charge(only_fixed = False)
            self.model_cube[self.axes_manager._getitem_tuple][
            self.channel_switches] = self.__call__(
                non_convolved = not self.convolved, onlyactive = True)
            if out_of_range_to_nan is True:
                self.model_cube[self.axes_manager._getitem_tuple][
                self.channel_switches == False] = np.nan
            i += 1
            pbar.update(i)
            
# TODO: port it                    
#    def generate_chisq(self, degrees_of_freedom = 'auto') :
#        if self.spectrum.variance is None:
#            self.spectrum.estimate_variance()
#        variance = self.spectrum.variance[self.channel_switches]
#        differences = (self.model_cube - self.spectrum.data)[self.channel_switches]
#        self.chisq = np.sum(differences**2 / variance, 0)
#        if degrees_of_freedom == 'auto':
#            self.red_chisq = self.chisq / \
#            (np.sum(np.ones(self.spectrum.energydimension)[self.channel_switches]) \
#            - len(self.p0) -1)
#            print "Degrees of freedom set to auto"
#            print "DoF = ", len(self.p0)
#        elif type(degrees_of_freedom) is int :
#            self.red_chisq = self.chisq / \
#            (np.sum(np.ones(self.spectrum.energydimension)[self.channel_switches]) \
#            - degrees_of_freedom -1)
#        else:
#            print "degrees_of_freedom must be on interger type."
#            print "The red_chisq could not been calculated"

    def _set_p0(self):
        p0 = None
        for component in self:
            if component.active:
                for param in component.free_parameters:
                    if p0 is not None:
                        p0 = (p0 + [param.value,] 
                        if not isinstance(param.value, list) 
                        else p0 + param.value)
                    else:
                        p0 = ([param.value,] 
                        if not isinstance(param.value, list) 
                        else param.value)
        self.p0 = tuple(p0)
    
    def set_boundaries(self):
        """Generate the boundary list.
        
        Necessary before fitting with a boundary awared optimizer
        """
        self.free_parameters_boundaries = []
        for component in self:
            if component.active:
                for param in component.free_parameters:
                    if param._number_of_elements == 1:
                        self.free_parameters_boundaries.append((
                        param._bounds))
                    else:
                        self.free_parameters_boundaries.extend((
                        param._bounds))
                        
    def set_mpfit_parameters_info(self):
        self.mpfit_parinfo = []
        for component in self:
            if component.active:
                for param in component.free_parameters:
                    if param._number_of_elements == 1:
                        limited = [False,False]
                        limits = [0,0]
                        if param.bmin is not None:
                            limited[0] = True
                            limits[0] = param.bmin
                        if param.bmax is not None:
                            limited[1] = True
                            limits[1] = param.bmax
                        self.mpfit_parinfo.append(
                        {'limited' : limited,
                         'limits' : limits})

    def set(self):
        """ Store the parameters of the current coordinates into the 
        parameters array.
        
        If the parameters array has not being defined yet it creates it filling 
        it with the current parameters."""
        for component in self:
            component.store_current_parameters_in_map(
            tuple(self.axes_manager._indexes))

    def charge(self, only_fixed = False):
        """Charge the parameters for the current spectrum from the parameters 
        array
        
        Parameters
        ----------
        only_fixed : bool
            If True, only the fixed parameters will be charged.
        """
        switch_aap = (False != self.auto_update_plot)
        if switch_aap is True:
            self.set_auto_update_plot(False)
        for component in self:
            component.charge_value_from_map(
            tuple(self.axes_manager._indexes), only_fixed = 
            only_fixed)
        if switch_aap is True:
            self.set_auto_update_plot(True)
            self.update_plot()

    def update_plot(self):
        if self.spectrum._plot is not None:
            try:
                for line in self.spectrum._plot.spectrum_plot.ax_lines:
                        line.update()
            except:
                self.disconnect_parameters2update_plot()
                
    def _charge_p0(self, p_std = None):
        """Charge the free data for the current coordinates (x,y) from the
        p0 array.
        
        Parameters
        ----------
        p_std : array
            array containing the corresponding standard deviation
        """
        comp_p_std = None
        counter = 0
        for component in self: # Cut the parameters list
            if component.active is True:
                if p_std is not None:
                    comp_p_std = p_std[counter: counter + component._nfree_param]
                component.charge(
                self.p0[counter: counter + component._nfree_param], 
                comp_p_std, onlyfree = True)
                counter += component._nfree_param

    # Defines the functions for the fitting process -------------------------
    def _model2plot(self, axes_manager, out_of_range2nans = True):
        old_axes_manager = None
        if axes_manager is not self.axes_manager:
            old_axes_manager = self.axes_manager
            self.axes_manager = axes_manager
            self.charge()
        s = self.__call__(non_convolved=False, onlyactive=True)
        if old_axes_manager is not None:
            self.axes_manager = old_axes_manager
            self.charge()
        if out_of_range2nans is True:
            ns = np.zeros((self.axis.axis.shape))
            ns[:] = np.nan
            ns[self.channel_switches] = s
            s = ns
        return s
    
    def __call__(self, non_convolved = False, onlyactive = False) :
        """Returns the corresponding model for the current coordinates
        
        Parameters
        ----------
        non_convolved : bool
            If True it will return the deconvolved model
        only_active : bool
            If True, only the active components will be used to build the model.
            
        cursor: 1 or 2
        
        Returns
        -------
        numpy array
        """
            
        if self.convolved is False or non_convolved is True:
            axis = self.axis.axis[self.channel_switches]
            sum_ = np.zeros(len(axis))
            if onlyactive is True:
                for component in self: # Cut the parameters list
                    if component.active:
                        np.add(sum_, component.function(axis),
                        sum_)
                return sum_
            else:
                for component in self: # Cut the parameters list
                    np.add(sum_, component.function(axis),
                     sum_)
                return sum_

        else: # convolved
            counter = 0
            sum_convolved = np.zeros(len(self.convolution_axis))
            sum_ = np.zeros(len(self.axis.axis))
            for component in self: # Cut the parameters list
                if onlyactive :
                    if component.active:
                        if component.convolved:
                            np.add(sum_convolved,
                            component.function(
                            self.convolution_axis), sum_convolved)
                        else:
                            np.add(sum_,
                            component.function(self.axis.axis), sum_)
                        counter+=component._nfree_param
                else :
                    if component.convolved:
                        np.add(sum_convolved,
                        component.function(self.convolution_axis),
                        sum_convolved)
                    else:
                        np.add(sum_, component.function(self.axis.axis),
                        sum_)
                    counter+=component._nfree_param
            to_return = sum_ + np.convolve(
                self.low_loss(self.axes_manager), 
                sum_convolved, mode="valid")
            to_return = to_return[self.channel_switches]
            return to_return

    # TODO: the way it uses the axes
    def set_data_range_in_pixels(self, i1 = None, i2 = None):
        """Use only the selected spectral range in the fitting routine.
        
        Parameters
        ----------
        i1 : Int
        i2 : Int
        
        Notes
        -----
        To use the full energy range call the function without arguments.
        """

        self.backup_channel_switches = copy.copy(self.channel_switches)
        self.channel_switches[:] = False
        self.channel_switches[i1:i2] = True
        if self.auto_update_plot is True:
            self.update_plot()
            
    @interactive_range_selector   
    def set_data_range_in_units(self, x1 = None, x2 = None):
        """Use only the selected spectral range defined in its own units in the 
        fitting routine.
        
        Parameters
        ----------
        E1 : None or float
        E2 : None or float
        
        Notes
        -----
        To use the full energy range call the function without arguments.
        """
        i1, i2 = self.axis.value2index(x1), self.axis.value2index(x2)
        self.set_data_range_in_pixels(i1, i2)

    def remove_data_range_in_pixels(self, i1 = None, i2= None):
        """Removes the data in the given range from the data range that will be 
        used by the fitting rountine
        
        Parameters
        ----------
        x1 : None or float
        x2 : None or float
        """
        self.channel_switches[i1:i2] = False
        if self.auto_update_plot is True:
            self.update_plot()

    @interactive_range_selector    
    def remove_data_range_in_units(self, x1 = None, x2= None):
        """Removes the data in the given range from the data range that will be 
        used by the fitting rountine
        
        Parameters
        ----------
        x1 : None or float
        x2 : None or float
        
        """
        i1, i2 = self.axis.value2index(x1), self.axis.value2index(x2)
        self.remove_data_range_in_pixels(i1, i2)
        
    def reset_data_range(self):
        '''Resets the data range'''
        self.set_data_range_in_pixels()
    
    def add_data_range_in_pixels(self, i1 = None, i2= None):
        """Adds the data in the given range from the data range that will be 
        used by the fitting rountine
        
        Parameters
        ----------
        x1 : None or float
        x2 : None or float
        """
        self.channel_switches[i1:i2] = True
        if self.auto_update_plot is True:
            self.update_plot()

    @interactive_range_selector    
    def add_data_range_in_units(self, x1 = None, x2= None):
        """Adds the data in the given range from the data range that will be 
        used by the fitting rountine
        
        Parameters
        ----------
        x1 : None or float
        x2 : None or float
        
        """
        i1, i2 = self.axis.value2index(x1), self.axis.value2index(x2)
        self.add_data_range_in_pixels(i1, i2)
        
    def reset_the_data_range(self):
        self.channel_switches[:] = True
        if self.auto_update_plot is True:
            self.update_plot()

    def _model_function(self,param):

        if self.convolved is True:
            counter = 0
            sum_convolved = np.zeros(len(self.convolution_axis))
            sum = np.zeros(len(self.axis.axis))
            for component in self: # Cut the parameters list
                if component.active is True:
                    if component.convolved is True:
                        np.add(sum_convolved, component(param[\
                        counter:counter+component._nfree_param],
                        self.convolution_axis), sum_convolved)
                    else:
                        np.add(sum, component(param[counter:counter + \
                        component._nfree_param], self.axis.axis), sum)
                    counter+=component._nfree_param

            return (sum + np.convolve(self.low_loss(self.axes_manager), 
                                      sum_convolved,mode="valid"))[
                                      self.channel_switches]

        else:
            axis = self.axis.axis[self.channel_switches]
            counter = 0
            first = True
            for component in self: # Cut the parameters list
                if component.active is True:
                    if first is True:
                        sum = component(param[counter:counter + \
                        component._nfree_param],axis)
                        first = False
                    else:
                        sum += component(param[counter:counter + \
                        component._nfree_param], axis)
                    counter += component._nfree_param
            return sum

    def _jacobian(self,param, y, weights = None):
        if self.convolved is True:
            counter = 0
            grad = np.zeros(len(self.axis.axis))
            for component in self: # Cut the parameters list
                if component.active:
                    component.charge(param[counter:counter + \
                    component._nfree_param] , onlyfree = True)
                    if component.convolved:
                        for parameter in component.free_parameters :
                            par_grad = np.convolve(
                            parameter.grad(self.convolution_axis), 
                            self.low_loss(self.axes_manager), 
                            mode="valid")
                            if parameter._twins:
                                for parameter in parameter._twins:
                                    np.add(par_grad, np.convolve(
                                    parameter.grad(
                                    self.convolution_axis), 
                                    self.low_loss(self.axes_manager), 
                                    mode="valid"), par_grad)
                            grad = np.vstack((grad, par_grad))
                        counter += component._nfree_param
                    else:
                        for parameter in component.free_parameters :
                            par_grad = parameter.grad(self.axis.axis)
                            if parameter._twins:
                                for parameter in parameter._twins:
                                    np.add(par_grad, parameter.grad(
                                    self.axis.axis), par_grad)
                            grad = np.vstack((grad, par_grad))
                        counter += component._nfree_param
            if weights is None:
                return grad[1:, self.channel_switches]
            else:
                return grad[1:, self.channel_switches] * weights
        else:
            axis = self.axis.axis[self.channel_switches]
            counter = 0
            grad = axis
            for component in self: # Cut the parameters list
                if component.active:
                    component.charge(param[counter:counter + \
                    component._nfree_param] , onlyfree = True)
                    for parameter in component.free_parameters :
                        par_grad = parameter.grad(axis)
                        if parameter._twins:
                            for parameter in parameter._twins:
                                np.add(par_grad, parameter.grad(
                                axis), par_grad)
                        grad = np.vstack((grad, par_grad))
                    counter += component._nfree_param
            if weights is None:
                return grad[1:,:]
            else:
                return grad[1:,:] * weights
        
    def _function4odr(self,param,x):
        return self._model_function(param)
    
    def _jacobian4odr(self,param,x):
        return self._jacobian(param, x)
                
    def multifit(self, mask = None, fitter = None, 
                 charge_only_fixed = False, grad = False, autosave = False, 
                 autosave_every = 10, bounded = False, **kwargs):
        
        if fitter is None:
            fitter = preferences.Model.default_fitter
            print('Fitter: %s' % fitter) 
        if autosave is not False:
            fd, autosave_fn = tempfile.mkstemp(prefix = 'hyperspy_autosave-', 
            dir = '.', suffix = '.npz')
            os.close(fd)
            autosave_fn = autosave_fn[:-4]
            messages.information(
            "Autosaving each %s pixels to %s.npz" % (autosave_every, 
                                                     autosave_fn))
            messages.information(
            "When multifit finishes its job the file will be deleted")
        if mask is not None and \
        (mask.shape != tuple(self.axes_manager.navigation_shape)):
           messages.warning_exit(
           "The mask must be an array with the same espatial dimensions as the" 
           "navigation shape, %s" % self.axes_manager.navigation_shape)
        masked_elements = 0 if mask is None else mask.sum()
        pbar = progressbar.progressbar(
        maxval = (np.cumprod(self.axes_manager.navigation_shape)[-1] - 
        masked_elements))
        if bounded is True:
            if fitter == 'mpfit':
                self.set_mpfit_parameters_info()
                bounded = None
            elif fitter in ("tnc", "l_bfgs_b"):
                self.set_boundaries()
                bounded = None
            else:
                messages.information(
                "The chosen fitter does not suppport bounding."
                "If you require boundinig please select one of the following"
                "fitters instead: mpfit, tnc, l_bfgs_b")
                bounded = False
        i = 0
        for index in np.ndindex(tuple(self.axes_manager.navigation_shape)):
            if mask is None or not mask[index]:
                self.axes_manager.set_not_slicing_indexes(index)
                self.charge(only_fixed = charge_only_fixed)
                self.fit(fitter = fitter, grad = grad, bounded = bounded, 
                         **kwargs)
                i += 1
                pbar.update(i)
            if autosave is True and i % autosave_every  == 0:
                self.save_parameters2file(autosave_fn)
        pbar.finish()
        if autosave is True:
            messages.information(
            'Deleting the temporary file %s pixels' % (autosave_fn + 'npz'))
            os.remove(autosave_fn + '.npz')

            
    def save_parameters2file(self,filename):
        """Save the parameters array in binary format"""
        kwds = {}
        i = 0
        for component in self:
            cname = component.name.lower().replace(' ', '_')
            for param in component.parameters:
                pname = param.name.lower().replace(' ', '_')
                kwds['%s_%s.%s' % (i, cname, pname)] = param.map
            i += 1
        np.savez(filename, **kwds)

    def load_parameters_from_file(self,filename):
        """Loads the parameters array from  a binary file written with the
        'save_parameters2file' function"""
        
        f = np.load(filename)
        i = 0
        for component in self: # Cut the parameters list
            cname = component.name.lower().replace(' ', '_')
            for param in component.parameters:
                pname = param.name.lower().replace(' ', '_')
                param.map = f['%s_%s.%s' % (i, cname, pname)]
            i += 1
                
        self.charge()
           
    def plot(self, auto_update_plot = True):
        """Plots the current spectrum to the screen and a map with a cursor to 
        explore the SI.
        """
        
        # If new coordinates are assigned
        self.spectrum.plot()
        _plot = self.spectrum._plot
        l1 = _plot.spectrum_plot.ax_lines[0]
        color = l1.line.get_color()
        l1.line_properties_helper(color, 'scatter')
        l1.set_properties()
        
        l2 = hyperspy.drawing.spectrum.SpectrumLine()
        l2.data_function = self._model2plot
        l2.line_properties_helper('blue', 'line')        
        # Add the line to the figure
          
        _plot.spectrum_plot.add_line(l2)
        l2.plot()
        self.connect_parameters2update_plot()
        on_figure_window_close(_plot.spectrum_plot.figure, 
                                      self.disconnect_parameters2update_plot)
        self.set_auto_update_plot(True)
        self._plot = self.spectrum._plot
        # TODO Set autoupdate to False on close
        
    def set_current_values_to(self, components_list = None, mask = None):
        if components_list is None:
            components_list = []
            for comp in self:
                if comp.active:
                    components_list.append(comp)
        for comp in components_list:
            for parameter in comp.parameters:
                parameter.set_current_value_to(mask = mask)
                
    def _enable_ext_bounding(self,components = None):
        """
        """
        if components is None :
            components = self
        for component in components:
            for parameter in component.parameters:
                parameter.ext_bounded = True
    def _disable_ext_bounding(self,components = None):
        """
        """
        if components is None :
            components = self
        for component in components:
            for parameter in component.parameters:
                parameter.ext_bounded = False
                
    def export_results(self, folder=None, format=None, save_std=False,
                       only_free=True, only_active = True):
        """Export the results of the parameters of the model to the desired
        folder.
        
        Parameters
        ----------
        folder : str or None
            The path to the folder where the file will be saved. If `None` the
            current folder is used by default.
        format : str
            The format to which the data will be exported. It must be the
            extension of any format supported by Hyperspy. If None, the default
            format for exporting as defined in the `Preferences` will be used.
        save_std : bool
            If True, also the standard deviation will be saved.
        only_free : bool
            If True, only the value of the parameters that are free will be
            exported.
        only_active : bool
            If True, only the value of the active parameters will be exported.
            
        Notes
        -----
        The name of the files will be determined by each the Component and
        each Parameter name attributes. Therefore, it is possible to customise
        the file names modify the name attributes.
              
        """
        for component in self:
            if only_active is False or component.active is True:
                component.export(folder=folder, format=format,
                                 save_std=save_std, only_free=only_free)
                                 
    def plot_results(self, only_free=True, only_active = True):
        """Plot the value of the parameters of the model
        
        Parameters
        ----------

        only_free : bool
            If True, only the value of the parameters that are free will be
            plotted.
        only_active : bool
            If True, only the value of the active parameters will be plotted.
            
        Notes
        -----
        The name of the files will be determined by each the Component and
        each Parameter name attributes. Therefore, it is possible to customise
        the file names modify the name attributes.
              
        """
        for component in self:
            if only_active is False or component.active is True:
                component.plot(only_free=only_free)
                
    def print_current_values(self, only_free=True):
        """Print the value of each parameter of the model.
        
        Parameters
        ----------
        only_free : bool
            If True, only the value of the parameters that are free will be
            printed.
        """
        print "Components\tParameter\tValue"
        for component in self:
            if component.active is True:
                if component.name:
                    print(component.name)
                else:
                    print(component._id_name)
                parameters = component.free_parameters if only_free \
                    else component.parameter
                for parameter in parameters:
                    print("\t\t%s\t%f" % (parameter.name, parameter.value))

        
