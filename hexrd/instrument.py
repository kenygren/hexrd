# -*- coding: utf-8 -*-
#! /usr/bin/env python
# ============================================================
# Copyright (c) 2012, Lawrence Livermore National Security, LLC.
# Produced at the Lawrence Livermore National Laboratory.
# Written by Joel Bernier <bernier2@llnl.gov> and others.
# LLNL-CODE-529294.
# All rights reserved.
#
# This file is part of HEXRD. For details on dowloading the source,
# see the file COPYING.
#
# Please also see the file LICENSE.
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License (as published by the Free
# Software Foundation) version 2.1 dated February 1999.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the IMPLIED WARRANTY OF MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE. See the terms and conditions of the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this program (see file LICENSE); if not, write to
# the Free Software Foundation, Inc., 59 Temple Place, Suite 330,
# Boston, MA 02111-1307 USA or visit <http://www.gnu.org/licenses/>.
# ============================================================
"""
Created on Fri Dec  9 13:05:27 2016

@author: bernier2
"""
from __future__ import print_function

import os

import yaml

import numpy as np

from scipy import ndimage

from gridutil import cellIndices, make_tolerance_grid
from hexrd import matrixutil as mutil
from hexrd.xrd.transforms_CAPI import anglesToGVec, \
                                      detectorXYToGvec, \
                                      gvecToDetectorXY, \
                                      makeDetectorRotMat, \
                                      makeOscillRotMat, \
                                      makeEtaFrameRotMat, \
                                      makeRotMatOfExpMap, \
                                      mapAngle, \
                                      oscillAnglesOfHKLs
from hexrd.xrd import xrdutil
from hexrd import constants as ct

from hexrd.xrd.distortion import GE_41RT  # BAD, VERY BAD!!! FIX!!!

beam_energy_DFLT = 65.351
beam_vec_DFLT = ct.beam_vec

eta_vec_DFLT = ct.eta_vec

panel_id_DFLT = "generic"
nrows_DFLT = 2048
ncols_DFLT = 2048
pixel_size_DFLT = (0.2, 0.2)

tilt_angles_DFLT = np.zeros(3)
t_vec_d_DFLT = np.r_[0., 0., -1000.]

chi_DFLT = 0.
t_vec_s_DFLT = np.zeros(3)


def calc_beam_vec(azim, pola):
    """
    Calculate unit beam propagation vector from
    spherical coordinate spec in DEGREES

    ...MAY CHANGE; THIS IS ALSO LOCATED IN XRDUTIL!
    """
    tht = np.radians(azim)
    phi = np.radians(pola)
    bv = np.r_[
        np.sin(phi)*np.cos(tht),
        np.cos(phi),
        np.sin(phi)*np.sin(tht)]
    return -bv


def calc_angles_from_beam_vec(bvec):
    """
    Return the azimuth and polar angle from a beam
    vector
    """
    bvec = np.atleast_2d(bvec).reshape(3, 1)
    nvec = mutil.unitVector(-bvec)
    azim = float(
        np.degrees(
            0.5*np.pi + np.arctan2(nvec[0], nvec[2])
        )
    )
    pola = float(np.degrees(np.arccos(nvec[1])))
    return azim, pola
    
    


def migrate_instrument_config(instrument_config):
    """utility function to generate old instrument config dictionary"""
    cfg_list = []
    for detector_id in instrument_config['detectors']:
        cfg_list.append(
            dict(
                detector=instrument_config['detectors'][detector_id],
                oscillation_stage=instrument_config['oscillation_stage'],
            )
        )
    return cfg_list


class HEDMInstrument(object):
    """
    * Distortion needs to be moved to a class with registry; tuple unworkable
    * where should reference eta be defined? currently set to default config
    """
    def __init__(self, instrument_config=None,
                 image_series=None,
                 instrument_name="instrument"):
        self._id = instrument_name

        if instrument_config is None:
            self._num_panels = 1
            self._beam_energy = beam_energy_DFLT
            self._beam_vector = beam_vec_DFLT

            self._eta_vector = eta_vec_DFLT

            self._detectors = dict(
                panel_id_DFLT=PlanarDetector(
                    rows=nrows_DFLT, cols=ncols_DFLT,
                    pixel_size=pixel_size_DFLT,
                    tvec=t_vec_d_DFLT,
                    tilt=tilt_angles_DFLT,
                    bvec=self._beam_vector,
                    evec=self._eta_vector,
                    distortion=None),
                )

            self._tvec = t_vec_s_DFLT
            self._chi = chi_DFLT
        else:
            self._num_panels = len(instrument_config['detectors'])
            self._beam_energy = instrument_config['beam']['energy']  # keV
            self._beam_vector = calc_beam_vec(
                instrument_config['beam']['vector']['azimuth'],
                instrument_config['beam']['vector']['polar_angle'],
                )
            ct.eta_vec
            # now build detector dict
            detector_ids = instrument_config['detectors'].keys()
            pixel_info = [instrument_config['detectors'][i]['pixels']
                          for i in detector_ids]
            affine_info = [instrument_config['detectors'][i]['transform']
                           for i in detector_ids]
            distortion = []
            for i in detector_ids:
                try:
                    distortion.append(
                        instrument_config['detectors'][i]['distortion']
                        )
                except KeyError:
                    distortion.append(None)
            det_list = []
            for pix, xform, dist in zip(pixel_info, affine_info, distortion):
                # HARD CODED GE DISTORTION !!! FIX
                dist_tuple = None
                if dist is not None:
                    dist_tuple = (GE_41RT, dist['parameters'])

                det_list.append(
                    PlanarDetector(
                        rows=pix['rows'], cols=pix['columns'],
                        pixel_size=pix['size'],
                        tvec=xform['t_vec_d'],
                        tilt=xform['tilt_angles'],
                        bvec=self._beam_vector,
                        evec=ct.eta_vec,
                        distortion=dist_tuple)
                    )
                pass
            self._detectors = dict(zip(detector_ids, det_list))

            self._tvec = np.r_[instrument_config['oscillation_stage']['t_vec_s']]
            self._chi = instrument_config['oscillation_stage']['chi']

        return

    # properties for physical size of rectangular detector
    @property
    def id(self):
        return self._id

    @property
    def num_panels(self):
        return self._num_panels

    @property
    def detectors(self):
        return self._detectors

    @property
    def tvec(self):
        return self._tvec

    @tvec.setter
    def tvec(self, x):
        x = np.array(x).flatten()
        assert len(x) == 3, 'input must have length = 3'
        self._tvec = x

    @property
    def chi(self):
        return self._chi

    @chi.setter
    def chi(self, x):
        self._chi = float(x)

    @property
    def beam_energy(self):
        return self._beam_energy

    @beam_energy.setter
    def beam_energy(self, x):
        self._beam_energy = float(x)

    @property
    def beam_wavelength(self):
        return ct.keVToAngstrom(self.beam_energy)

    @property
    def beam_vector(self):
        return self._beam_vector

    @beam_vector.setter
    def beam_vector(self, x):
        x = np.array(x).flatten()
        assert len(x) == 3 and sum(x*x) > 1-ct.sqrt_epsf, \
            'input must have length = 3 and have unit magnitude'
        self._beam_vector = x
        # ...maybe change dictionary item behavior for 3.x compatibility?
        for detector_id in self.detectors:
            panel = self.detectors[detector_id]
            panel.bvec = self._beam_vector

    @property
    def eta_vector(self):
        return self._eta_vector

    @eta_vector.setter
    def eta_vector(self, x):
        x = np.array(x).flatten()
        assert len(x) == 3 and sum(x*x) > 1-ct.sqrt_epsf, \
            'input must have length = 3 and have unit magnitude'
        self._eta_vector = x
        # ...maybe change dictionary item behavior for 3.x compatibility?
        for detector_id in self.detectors:
            panel = self.detectors[detector_id]
            panel.evec = self._eta_vector

    # methods
    def write_config(self, filename, calibration_dict={}):
        """ WRITE OUT YAML FILE """
        # initialize output dictionary

        par_dict = {}

        azim, pola = calc_angles_from_beam_vec(self.beam_vector)
        beam = dict(
            energy=self.beam_energy,
            vector=dict(
                azimuth=azim,
                polar_angle=pola,
            )
        )
        par_dict['beam'] = beam
        
        if calibration_dict:
            par_dict['calibration_crystal'] = calibration_dict
        
        ostage = dict(
            chi=self.chi,
            t_vec_s=self.tvec.tolist()
        )
        par_dict['oscillation_stage'] = ostage
        
        det_names = self.detectors.keys()
        det_dict = dict.fromkeys(det_names)
        for det_name in det_names:
            panel = self.detectors[det_name]
            pdict = panel.config_dict(self.chi, self.tvec)
            det_dict[det_name] = pdict['detector']
        par_dict['detectors'] = det_dict
        with open(filename, 'w') as f:
            yaml.dump(par_dict, stream=f)
        return par_dict

    
    def pull_spots(self, plane_data, grain_params,
                   imgser_dict,
                   tth_tol=0.25, eta_tol=1., ome_tol=1.,
                   npdiv=1, threshold=10,
                   dirname='results', filename=None, save_spot_list=False,
                   quiet=True, lrank=1):


        '''first find valid G-vectors'''
        bMat = plane_data.latVecOps['B']

        rMat_c = makeRotMatOfExpMap(grain_params[:3])
        tVec_c = grain_params[3:6]
        vInv_s = grain_params[6:]

        # vstacked G-vector id, h, k, l
        full_hkls = xrdutil._fetch_hkls_from_planedata(plane_data)

        # All possible bragg conditions as vstacked [tth, eta, ome] for
        # each omega solution
        angList = np.vstack(
            oscillAnglesOfHKLs(
                full_hkls[:, 1:], self.chi,
                rMat_c, bMat, self.beam_wavelength,
                vInv=vInv_s,
            )
        )

        # grab omega ranges from first imageseries
        # ...NOTE THAT THEY ARE ALL ASSUMED TO HAVE SAME OMEGAS
        oims0 = imgser_dict[imgser_dict.keys()[0]]
        ome_ranges = [(ct.d2r*i['ostart'], ct.d2r*i['ostop'])
                      for i in oims0.omegawedges.wedges]

        # delta omega in DEGREES grabbed from first imageseries
        # ...put in a check that they are all the same???
        delta_ome = oims0.omega[0, 1] - oims0.omega[0, 0]

        # make omega grid for frame expansion around reference frame
        ndiv_ome, ome_del = make_tolerance_grid(
            delta_ome, ome_tol, 1, adjust_window=True,
        )

        # generate structuring element for connected component labeling
        if len(ome_del) == 1:
            label_struct = ndimage.generate_binary_structure(2, lrank)
        else:
            label_struct = ndimage.generate_binary_structure(3, lrank)

        # filter by eta and omega ranges
        allAngs, allHKLs = xrdutil._filter_hkls_eta_ome(
            full_hkls, angList, [(-np.pi, np.pi), ], ome_ranges
            )

        # dilate angles tth and eta to patch corners
        nangs = len(allAngs)
        tol_vec = 0.5*np.radians(
            [-tth_tol, -eta_tol,
             -tth_tol,  eta_tol,
             tth_tol,  eta_tol,
             tth_tol, -eta_tol])
        patch_vertices = (np.tile(allAngs[:, :2], (1, 4)) \
            + np.tile(tol_vec, (nangs, 1))).reshape(4*nangs, 2)
        ome_dupl = np.tile(allAngs[:, 2], (4, 1)).T.reshape(len(patch_vertices), 1)

        '''loop over panels'''
        iRefl = 0
        for detector_id in self.detectors:
            # initialize output writer
            if filename is not None:
                output_dir = os.path.join(
                    dirname, detector_id
                    )
                if not os.path.exists(output_dir):
                    os.makedirs(output_dir)
                this_filename =  os.path.join(
                    output_dir, filename
                )
                pw = PatchDataWriter(this_filename)

            # grab panel
            panel = self.detectors[detector_id]
            instr_cfg = panel.config_dict(self.chi, self.tvec)
            native_area = panel.pixel_area  # pixel ref area

            # find points that fall on the panel
            det_xy, rMat_s = xrdutil._project_on_detector_plane(
                np.hstack([patch_vertices, ome_dupl]),
                panel.rmat, rMat_c, self.chi,
                panel.tvec, tVec_c, self.tvec,
                panel.distortion
                )
            tmp_xy, on_panel = panel.clip_to_panel(det_xy)

            # all vertices must be on...
            patch_is_on = np.all(on_panel.reshape(nangs, 4), axis=1)
            #nrefl_p += sum(patch_is_on)

            # grab hkls and gvec ids for this panel
            hkls_p = allHKLs[patch_is_on, 1:]
            hkl_ids = allHKLs[patch_is_on, 0]

            # reflection angles (voxel centers) and pixel size in (tth, eta)
            ang_centers = allAngs[patch_is_on, :]
            ang_pixel_size = panel.angularPixelSize(tmp_xy)

            # make the tth,eta patches for interpolation
            patches = xrdutil.make_reflection_patches(
                instr_cfg, ang_centers[:, :2], ang_pixel_size,
                tth_tol=tth_tol, eta_tol=eta_tol,
                rMat_c=rMat_c, tVec_c=tVec_c,
                distortion=panel.distortion,
                npdiv=npdiv, quiet=True,
                beamVec=self.beam_vector)

            # pull out the OmegaImageSeries for this panel from input dict
            ome_imgser = imgser_dict[detector_id]

            # grand loop over reflections for this panel
            for i_pt, patch in enumerate(patches):

                # grab hkl info
                hkl = hkls_p[i_pt, :]
                hkl_id = hkl_ids[i_pt]

                # strip relevant objects out of current patch
                vtx_angs, vtx_xy, conn, areas, xy_eval, ijs = patch
                prows, pcols = areas.shape

                tth_edges = vtx_angs[0][0, :]
                delta_tth = tth_edges[1] - tth_edges[0]

                eta_edges = vtx_angs[1][:, 0]
                delta_eta = eta_edges[1] - eta_edges[0]

                # need to reshape eval pts for interpolation
                xy_eval = np.vstack([xy_eval[0].flatten(),
                                     xy_eval[1].flatten()]).T

                # the evaluation omegas;
                # expand about the central value using tol vector
                ome_eval = np.degrees(ang_centers[i_pt, 2]) + ome_del

                # ...vectorize the omega_to_frame function to avoid loop?
                frame_indices = [
                    ome_imgser.omega_to_frame(ome)[0] for ome in ome_eval
                ]
                if np.any(frame_indices == -1):
                    if not quiet:
                        msg = "window for (%d%d%d) falls outside omega range"\
                            % tuple(hkl)
                        print(msg)
                    continue
                else:
                    peak_id = -999
                    sum_int = None
                    max_int = None
                    meas_angs = None
                    meas_xy = None
                    
                    patch_data = np.zeros((len(frame_indices), prows, pcols))
                    ome_edges = np.hstack(
                        [ome_imgser.omega[frame_indices][:, 0],
                         ome_imgser.omega[frame_indices][-1, 1]]
                    )
                    for i, i_frame in enumerate(frame_indices):
                        patch_data[i] = \
                            panel.interpolate_bilinear(
                                    xy_eval,
                                    ome_imgser[i_frame],
                            ).reshape(prows, pcols)*(areas/float(native_area))
                        pass

                    # now have interpolated patch data...
                    labels, num_peaks = ndimage.label(
                        patch_data > threshold, structure=label_struct
                    )
                    slabels = np.arange(1, num_peaks + 1)
                    if num_peaks > 0:
                        peak_id = iRefl
                        coms = np.array(
                            ndimage.center_of_mass(
                                patch_data, labels=labels, index=slabels
                                )
                            )
                        if num_peaks > 1:
                            center = np.r_[patch_data.shape]*0.5
                            com_diff = coms - np.tile(center, (num_peaks, 1))
                            closest_peak_idx = np.argmin(np.sum(com_diff**2, axis=1))
                        else:
                            closest_peak_idx = 0
                            pass  # end multipeak conditional
                        coms = coms[closest_peak_idx]
                        meas_angs = np.hstack([
                            tth_edges[0] + (0.5 + coms[2])*delta_tth,
                            eta_edges[0] + (0.5 + coms[1])*delta_eta,
                            np.radians(ome_edges[0] + (0.5 + coms[0])*delta_ome)
                            ])

                        # intensities
                        #   - summed is 'integrated' over interpolated data
                        #   - max is max of raw input data
                        sum_int = np.sum(
                            patch_data[labels == slabels[closest_peak_idx]]
                        )
                        max_int = np.max(
                            [ome_imgser[i][ijs[0], ijs[1]] for i in frame_indices]
                        )
                        #max_int = np.max(
                        #    patch_data[labels == slabels[closest_peak_idx]]
                        #    )
                        
                        # need xy coords
                        gvec_c = anglesToGVec(
                            meas_angs, 
                            chi=self.chi, 
                            rMat_c=rMat_c,
                            bHat_l=self.beam_vector)
                        rMat_s = makeOscillRotMat([self.chi, meas_angs[2]])
                        meas_xy = gvecToDetectorXY(
                            gvec_c,
                            panel.rmat, rMat_s, rMat_c,
                            panel.tvec, self.tvec, tVec_c,
                            beamVec=self.beam_vector)
                        if panel.distortion is not None:
                            """...FIX THIS!!!"""
                            meas_xy = panel.distortion[0](
                                np.atleast_2d(meas_xy),
                                panel.distortion[1],
                                invert=True).flatten()
                            pass                        
                        pass
                    
                    # write output
                    if filename is not None:
                        pw.dump_patch(
                            peak_id, hkl_id, hkl, sum_int, max_int,
                            ang_centers[i_pt], meas_angs, meas_xy)
                    iRefl += 1
                    pass  # end patch conditional
                pass  # end patch loop
            if filename is not None:
                del(pw)
            pass  # end detector loop
        return
    pass  # end class: HEDMInstrument


class PlanarDetector(object):
    """
    base class for 2D planar, rectangular row-column detector
    """

    __pixelPitchUnit = 'mm'
    __delta_eta = np.radians(10.)

    def __init__(self,
                 rows=2048, cols=2048,
                 pixel_size=(0.2, 0.2),
                 tvec=np.r_[0., 0., -1000.],
                 tilt=ct.zeros_3,
                 bvec=ct.beam_vec,
                 evec=ct.eta_vec,
                 panel_buffer=None,
                 distortion=None):
        """
        panel buffer is in pixels...

        """
        self._rows = rows
        self._cols = cols

        self._pixel_size_row = pixel_size[0]
        self._pixel_size_col = pixel_size[1]

        self._panel_buffer = panel_buffer

        self._tvec = np.array(tvec).flatten()
        self._tilt = tilt

        self._bvec = np.array(bvec).flatten()
        self._evec = np.array(evec).flatten()

        self._distortion = distortion

        return

    # properties for physical size of rectangular detector
    @property
    def rows(self):
        return self._rows

    @rows.setter
    def rows(self, x):
        assert isinstance(x, int)
        self._rows = x

    @property
    def cols(self):
        return self._cols

    @cols.setter
    def cols(self, x):
        assert isinstance(x, int)
        self._cols = x

    @property
    def pixel_size_row(self):
        return self._pixel_size_row

    @pixel_size_row.setter
    def pixel_size_row(self, x):
        self._pixel_size_row = float(x)

    @property
    def pixel_size_col(self):
        return self._pixel_size_col

    @pixel_size_col.setter
    def pixel_size_col(self, x):
        self._pixel_size_col = float(x)

    @property
    def pixel_area(self):
        return self.pixel_size_row * self.pixel_size_col

    @property
    def panel_buffer(self):
        return self._panel_buffer

    @panel_buffer.setter
    def panel_buffer(self, x):
        """if not None, a buffer in mm (x, y)"""
        if x is not None:
            assert len(x) == 2
        self._panel_buffer = x

    @property
    def row_dim(self):
        return self.rows * self.pixel_size_row

    @property
    def col_dim(self):
        return self.cols * self.pixel_size_col

    @property
    def row_pixel_vec(self):
        return self.pixel_size_row*(0.5*(self.rows-1)-np.arange(self.rows))

    @property
    def row_edge_vec(self):
        return self.pixel_size_row*(0.5*self.rows-np.arange(self.rows+1))

    @property
    def col_pixel_vec(self):
        return self.pixel_size_col*(np.arange(self.cols)-0.5*(self.cols-1))

    @property
    def col_edge_vec(self):
        return self.pixel_size_col*(np.arange(self.cols+1)-0.5*self.cols)

    @property
    def corner_ul(self):
        return np.r_[-0.5 * self.col_dim,  0.5 * self.row_dim]

    @property
    def corner_ll(self):
        return np.r_[-0.5 * self.col_dim, -0.5 * self.row_dim]

    @property
    def corner_lr(self):
        return np.r_[0.5 * self.col_dim, -0.5 * self.row_dim]

    @property
    def corner_ur(self):
        return np.r_[0.5 * self.col_dim,  0.5 * self.row_dim]

    @property
    def tvec(self):
        return self._tvec

    @tvec.setter
    def tvec(self, x):
        x = np.array(x).flatten()
        assert len(x) == 3, 'input must have length = 3'
        self._tvec = x

    @property
    def tilt(self):
        return self._tilt

    @tilt.setter
    def tilt(self, x):
        assert len(x) == 3, 'input must have length = 3'
        self._tilt = np.array(x).squeeze()

    @property
    def bvec(self):
        return self._bvec

    @bvec.setter
    def bvec(self, x):
        x = np.array(x).flatten()
        assert len(x) == 3 and sum(x*x) > 1-ct.sqrt_epsf, \
            'input must have length = 3 and have unit magnitude'
        self._bvec = x

    @property
    def evec(self):
        return self._evec

    @evec.setter
    def evec(self, x):
        x = np.array(x).flatten()
        assert len(x) == 3 and sum(x*x) > 1-ct.sqrt_epsf, \
            'input must have length = 3 and have unit magnitude'
        self._evec = x

    @property
    def distortion(self):
        return self._distortion

    @distortion.setter
    def distortion(self, x):
        """
        Probably should make distortion a class...
        ***FIX THIS***
        """
        assert len(x) == 2 and hasattr(x[0], '__call__'), \
            'distortion must be a tuple: (<func>, params)'
        self._distortion = x

    @property
    def rmat(self):
        return makeDetectorRotMat(self.tilt)

    @property
    def normal(self):
        return self.rmat[:, 2]

    @property
    def beam_position(self):
        """
        returns the coordinates of the beam in the cartesian detector
        frame {Xd, Yd, Zd}.  NaNs if no intersection.
        """
        output = np.nan * np.ones(2)
        b_dot_n = np.dot(self.bvec, self.normal)
        if np.logical_and(
            abs(b_dot_n) > ct.sqrt_epsf,
            np.sign(b_dot_n) == -1
        ):
            u = np.dot(self.normal, self.tvec) / b_dot_n
            p2_l = u*self.bvec
            p2_d = np.dot(self.rmat.T, p2_l - self.tvec)
            output = p2_d[:2]
        return output

    # ...memoize???
    @property
    def pixel_coords(self):
        pix_i, pix_j = np.meshgrid(
            self.row_pixel_vec, self.col_pixel_vec,
            indexing='ij')
        return pix_i, pix_j

    # ...memoize???
    @property
    def pixel_angles(self):
        pix_i, pix_j = self.pixel_coords
        xy = np.ascontiguousarray(
            np.vstack([
                pix_j.flatten(), pix_i.flatten()
                ]).T
            )
        angs, g_vec = detectorXYToGvec(
            xy, self.rmat, ct.identity_3x3,
            self.tvec, ct.zeros_3, ct.zeros_3,
            beamVec=self.bvec, etaVec=self.evec)
        del(g_vec)
        tth = angs[0].reshape(self.rows, self.cols)
        eta = angs[1].reshape(self.rows, self.cols)
        return tth, eta

    def config_dict(self, chi, t_vec_s, sat_level=None):
        """
        """
        t_vec_s = np.atleast_1d(t_vec_s)
        
        d = dict(
            detector=dict(
                transform=dict(
                    tilt_angles=self.tilt,
                    t_vec_d=self.tvec.tolist(),
                ),
                pixels=dict(
                    rows=self.rows,
                    columns=self.cols,
                    size=[self.pixel_size_row, self.pixel_size_col],
                ),
            ),
            oscillation_stage=dict(
                chi=chi,
                t_vec_s=t_vec_s.tolist(),
            ),
        )
        if sat_level is not None:
            d['detector']['saturation_level'] = sat_level
        if self.distortion is not None:
            """...HARD CODED DISTORTION! FIX THIS!!!"""
            dist_d = dict(
                function_name='GE_41RT',
                parameters=self.distortion[1]
            )
            d['detector']['distortion'] = dist_d
        return d

    """
    ##################### METHODS
    """
    def cartToPixel(self, xy_det, pixels=False):
        """
        Convert vstacked array or list of [x,y] points in the center-based
        cartesian frame {Xd, Yd, Zd} to (i, j) edge-based indices

        i is the row index, measured from the upper-left corner
        j is the col index, measured from the upper-left corner

        if pixels=True, then (i,j) are integer pixel indices.
        else (i,j) are continuous coords
        """
        xy_det = np.atleast_2d(xy_det)

        npts = len(xy_det)

        tmp_ji = xy_det - np.tile(self.corner_ul, (npts, 1))
        i_pix = -tmp_ji[:, 1] / self.pixel_size_row - 0.5
        j_pix = tmp_ji[:, 0] / self.pixel_size_col - 0.5

        ij_det = np.vstack([i_pix, j_pix]).T
        if pixels:
            ij_det = np.array(np.round(ij_det), dtype=int)
        return ij_det

    def pixelToCart(self, ij_det):
        """
        Convert vstacked array or list of [i,j] pixel indices
        (or UL corner-based points) and convert to (x,y) in the
        cartesian frame {Xd, Yd, Zd}
        """
        ij_det = np.atleast_2d(ij_det)

        x = (ij_det[:, 1] + 0.5)*self.pixel_size_col\
            + self.corner_ll[0]
        y = (self.rows - ij_det[:, 0] - 0.5)*self.pixel_size_row\
            + self.corner_ll[1]
        return np.vstack([x, y]).T

    def angularPixelSize(self, xy, rMat_s=None, tVec_s=None, tVec_c=None):
        """
        Wraps xrdutil.angularPixelSize
        """
        # munge kwargs
        if rMat_s is None:
            rMat_s = ct.identity_3x3
        if tVec_s is None:
            tVec_s = ct.zeros_3x1
        if tVec_c is None:
            tVec_c = ct.zeros_3x1

        # call function
        ang_ps = xrdutil.angularPixelSize(
            xy, (self.pixel_size_row, self.pixel_size_col),
            self.rmat, rMat_s,
            self.tvec, tVec_s, tVec_c,
            distortion=self.distortion,
            beamVec=self.bvec, etaVec=self.evec)
        return ang_ps

    def clip_to_panel(self, xy, buffer_edges=True):
        """
        """
        xy = np.atleast_2d(xy)
        xlim = 0.5*self.col_dim
        ylim = 0.5*self.row_dim
        if buffer_edges and self.panel_buffer is not None:
            xlim -= self.panel_buffer[0]
            ylim -= self.panel_buffer[1]
        on_panel_x = np.logical_and(xy[:, 0] >= -xlim, xy[:, 0] <= xlim)
        on_panel_y = np.logical_and(xy[:, 1] >= -ylim, xy[:, 1] <= ylim)
        on_panel = np.logical_and(on_panel_x, on_panel_y)
        return xy[on_panel, :], on_panel

    def interpolate_bilinear(self, xy, img, pad_with_nans=True):
        """
        """
        is_2d = img.ndim == 2
        right_shape = img.shape[0] == self.rows and img.shape[1] == self.cols
        assert is_2d and right_shape,\
            "input image must be 2-d with shape (%d, %d)"\
            % (self.rows, self.cols)

        # initialize output with nans
        if pad_with_nans:
            int_xy = np.nan*np.ones(len(xy))
        else:
            int_xy = np.zeros(len(xy))

        # clip away points too close to or off the edges of the detector
        xy_clip, on_panel = self.clip_to_panel(xy, buffer_edges=True)

        # grab fractional pixel indices of clipped points
        ij_frac = self.cartToPixel(xy_clip)

        # get floors/ceils from array of pixel _centers_
        i_floor = cellIndices(self.row_pixel_vec, xy_clip[:, 1])
        j_floor = cellIndices(self.col_pixel_vec, xy_clip[:, 0])
        i_ceil = i_floor + 1
        j_ceil = j_floor + 1

        # first interpolate at top/bottom rows
        row_floor_int = \
            (j_ceil - ij_frac[:, 1])*img[i_floor, j_floor] \
            + (ij_frac[:, 1] - j_floor)*img[i_floor, j_ceil]
        row_ceil_int = \
            (j_ceil - ij_frac[:, 1])*img[i_ceil, j_floor] \
            + (ij_frac[:, 1] - j_floor)*img[i_ceil, j_ceil]

        # next interpolate across cols
        int_vals = \
            (i_ceil - ij_frac[:, 0])*row_floor_int \
            + (ij_frac[:, 0] - i_floor)*row_ceil_int
        int_xy[on_panel] = int_vals
        return int_xy

    def make_powder_rings(
            self, pd, merge_hkls=False, delta_eta=None, eta_period=None,
            rmat_s=ct.identity_3x3,  tvec_s=ct.zeros_3,
            tvec_c=ct.zeros_3):
        """
        """

        # for generating rings
        if delta_eta is None:
            delta_eta = self.__delta_eta
        if eta_period is None:
            eta_period = (-np.pi, np.pi)

        neta = int(360./float(delta_eta))
        eta = mapAngle(
            np.radians(
                delta_eta*np.linspace(0, neta-1, num=neta)
            ) + eta_period[0], eta_period
        )

        # in case you want to give it tth angles directly
        if hasattr(pd, '__len__'):
            tth = np.array(pd).flatten()
        else:
            if merge_hkls:
                tth_idx, tth_ranges = pd.getMergedRanges()
                tth = [0.5*sum(i) for i in tth_ranges]
            else:
                tth = pd.getTTh()
        angs = [np.vstack([i*np.ones(neta), eta, np.zeros(neta)]) for i in tth]

        # need xy coords and pixel sizes
        valid_ang = []
        valid_xy = []
        for i_ring in range(len(angs)):
            these_angs = angs[i_ring].T
            gVec_ring_l = anglesToGVec(these_angs, bHat_l=self.bvec)
            xydet_ring = gvecToDetectorXY(
                gVec_ring_l,
                self.rmat, rmat_s, ct.identity_3x3,
                self.tvec, tvec_s, tvec_c,
                beamVec=self.bvec)
            #
            xydet_ring, on_panel = self.clip_to_panel(xydet_ring)
            #
            valid_ang.append(these_angs[on_panel, :2])
            valid_xy.append(xydet_ring)
            pass
        return valid_ang, valid_xy

    def map_to_plane(self, pts, rmat, tvec):
        """
        map detctor points to specified plane

        by convention

        n * (u*pts_l - tvec) = 0

        [pts]_l = rmat*[pts]_m + tvec
        """
        # arg munging
        pts = np.atleast_2d(pts)
        npts = len(pts)

        # map plane normal & translation vector, LAB FRAME
        nvec_map_lab = rmat[:, 2].reshape(3, 1)
        tvec_map_lab = np.atleast_2d(tvec).reshape(3, 1)
        tvec_d_lab = np.atleast_2d(self.tvec).reshape(3, 1)

        # put pts as 3-d in panel CS and transform to 3-d lab coords
        pts_det = np.hstack([pts, np.zeros((npts, 1))])
        pts_lab = np.dot(self.rmat, pts_det.T) + tvec_d_lab

        # scaling along pts vectors to hit map plane
        u = np.dot(nvec_map_lab.T, tvec_map_lab) \
            / np.dot(nvec_map_lab.T, pts_lab)

        # pts on map plane, in LAB FRAME
        pts_map_lab = np.tile(u, (3, 1)) * pts_lab

        return np.dot(rmat.T, pts_map_lab - tvec_map_lab)[:2, :].T


"""UTILITIES"""


class PatchDataWriter(object):
    """
    """
    def __init__(self, filename):
        xy_str = '{:18}\t{:18}\t{:18}'
        ang_str = xy_str + '\t'
        self._header = \
            '{:6}\t{:6}\t'.format('# ID', 'PID') + \
            '{:3}\t{:3}\t{:3}\t'.format('H', 'K', 'L') + \
            '{:12}\t{:12}\t'.format('sum(int)', 'max(int)') + \
            ang_str.format('pred tth', 'pred eta', 'pred ome') + \
            ang_str.format('meas tth', 'meas eta', 'meas ome') + \
            xy_str.format('meas X', 'meas Y', 'meas ome')
        if isinstance(filename, file):
            self.fid = filename
        else:
            self.fid = open(filename, 'w')
        print(self._header, file=self.fid)

    def __del__(self):
        self.close()

    def close(self):
        self.fid.close()

    def dump_patch(self, peak_id, hkl_id,
                   hkl, spot_int, max_int,
                   pangs, mangs, xy):
        nans_tabbed_12 = '{:^12}\t{:^12}\t'
        nans_tabbed_18 = '{:^18}\t{:^18}\t{:^18}\t{:^18}\t{:^18}'
        output_str = \
            '{:<6d}\t{:<6d}\t'.format(int(peak_id), int(hkl_id)) + \
            '{:<3d}\t{:<3d}\t{:<3d}\t'.format(*np.array(hkl, dtype=int))
        if peak_id >= 0:
            output_str += \
                '{:<1.6e}\t{:<1.6e}\t'.format(spot_int, max_int) + \
                '{:<1.12e}\t{:<1.12e}\t{:<1.12e}\t'.format(*pangs) + \
                '{:<1.12e}\t{:<1.12e}\t{:<1.12e}\t'.format(*mangs) + \
                '{:<1.12e}\t{:<1.12e}'.format(xy[0], xy[1])
        else:
            output_str += \
                nans_tabbed_12.format(*np.ones(2)*np.nan) + \
                '{:<1.12e}\t{:<1.12e}\t{:<1.12e}\t'.format(*pangs) + \
                nans_tabbed_18.format(*np.ones(5)*np.nan)
        print(output_str, file=self.fid)
        return output_str
