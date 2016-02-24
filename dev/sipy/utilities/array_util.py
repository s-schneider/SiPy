from __future__ import absolute_import
from collections import defaultdict
import tempfile
import numpy as np
import os
import shutil
import matplotlib.pyplot as plt
#from mpl_toolkits.basemap import Basemap
from matplotlib.ticker import MaxNLocator
from obspy import UTCDateTime, Stream
from obspy.core import AttribDict
from obspy.geodetics.base import locations2degrees, gps2DistAzimuth, \
   kilometer2degrees
from obspy.taup import getTravelTimes
import scipy.interpolate as spi
import scipy as sp
import matplotlib.cm as cm
from obspy.signal.util import utlGeoKm,nextpow2
import ctypes as C
from obspy.core import Stream
import math
import warnings
from scipy.integrate import cumtrapz
from obspy.core import Stream
from obspy.signal.headers import clibsignal
from obspy.signal.invsim import cosTaper
from obspy.clients.fdsn import Client
from obspy.taup import TauPyModel
from sipy.utilities.base import stream2array


"""
Collection of useful functions for processing seismological array data

Author: S. Schneider 2016
"""


def attach_coordinates_to_traces(stream, inventory, event=None):
    """
    Function to add coordinates to traces.

    It extracts coordinates from a :class:`obspy.station.inventory.Inventory`
    object and writes them to each trace's stats attribute. If an event is
    given, the distance in degree will also be attached.

    :param stream: Waveforms for the array processing.
    :type stream: :class:`obspy.core.stream.Stream`
    :param inventory: Station metadata for waveforms
    :type inventory: :class:`obspy.station.inventory.Inventory`
    :param event: If the event is given, the event distance in degree will also
     be attached to the traces.
    :type event: :class:`obspy.core.event.Event`
    """
    # Get the coordinates for all stations
    coords = {}
    for network in inventory:
        for station in network:
            coords["%s.%s" % (network.code, station.code)] = \
                {"latitude": station.latitude,
                 "longitude": station.longitude,
                 "elevation": station.elevation}

    # Calculate the event-station distances.
    if event:
        event_lat = event.origins[0].latitude
        event_lng = event.origins[0].longitude
        for value in coords.values():
            value["distance"] = locations2degrees(
                value["latitude"], value["longitude"], event_lat, event_lng)

    # Attach the information to the traces.
    for trace in stream:
        station = ".".join(trace.id.split(".")[:2])
        value = coords[station]
        trace.stats.coordinates = AttribDict()
        trace.stats.coordinates.latitude = value["latitude"]
        trace.stats.coordinates.longitude = value["longitude"]
        trace.stats.coordinates.elevation = value["elevation"]
        if event:
            trace.stats.distance = value["distance"]

def get_coords(inventory, returntype="dict"):
    """
    Get the coordinates of the stations in the inventory, independently of the channels,
    better use for arrays, than the channel-dependent core.inventory.inventory.Inventory.get_coordinates() .
    returns the variable coords with entries: elevation (in km), latitude and longitude.
    :param inventory: Inventory to get the coordinates from
    :type inventory: obspy.core.inventory.inventory.Inventory

    :param coords: dictionary with stations of the inventory and its elevation (in km), latitude and longitude
    :type coords: dict

    :param return: type of desired return
    :type return: dictionary or numpy.array

    """
    if returntype == "dict":
        coords = {}
        for network in inventory:
            for station in network:
                coords["%s.%s" % (network.code, station.code)] = \
                    {"latitude": station.latitude,
                     "longitude": station.longitude,
                     "elevation": float(station.elevation) / 1000.0}

    if returntype == "array":
        nstats = len(inventory[0].stations)
        coords = np.empty((nstats, 3))
        if len(inventory.networks) == 1:
            i=0
            for network in inventory:
                for station in network:
                    coords[i,0] = station.latitude
                    coords[i,1] = station.longitude
                    coords[i,2] = float(station.elevation) / 1000.0
                    i += 1

    return coords


def __coordinate_values(inventory):
    geo = get_coords(inventory, returntype="dict")
    lats, lngs, hgt = [], [], []
    for coordinates in list(geo.values()):
        lats.append(coordinates["latitude"]),
        lngs.append(coordinates["longitude"]),
        hgt.append(coordinates["elevation"])
    return lats, lngs, hgt

def plot(inventory, projection="local"):
    """
    Function to plot the geometry of the array, 
    including its center of gravity and geometrical center

    :type inventory: obspy.core.inventory.inventory.Inventory
    :param inventory: Inventory to be plotted

    :type projection: strg, optional
    :param projection: The map projection. Currently supported are:

    * ``"global"`` (Will plot the whole world.)
    * ``"ortho"`` (Will center around the mean lat/long.)
    * ``"local"`` (Will plot around local events)   
    """
    if inventory:
        inventory.plot(projection, show=False)
        bmap = plt.gca().basemap

        grav = center_of_gravity(inventory)
        x, y = bmap(grav["longitude"], grav["latitude"])
        bmap.scatter(x, y, marker="x", c="red", s=40, zorder=20)
        plt.text(x, y, "Center of Gravity", color="red")

        geo = geometrical_center(inventory)
        x, y = bmap(geo["longitude"], geo["latitude"])
        bmap.scatter(x, y, marker="x", c="green", s=40, zorder=20)
        plt.text(x, y, "Geometrical Center", color="green")

        plt.show()

def center_of_gravity(inventory):
    lats, lngs, hgts = __coordinate_values(inventory)
    return {
        "latitude": np.mean(lats),
        "longitude": np.mean(lngs),
        "elevation": np.mean(hgts)}

def geometrical_center(inventory):
    lats, lngs, hgt = __coordinate_values(inventory)

    return {
        "latitude": (np.max(lats) +
                     np.min(lats)) / 2.0,
        "longitude": (np.max(lngs) +
                      np.min(lngs)) / 2.0,
        "absolute_height_in_km":
        (np.max(hgt) +
         np.min(hgt)) / 2.0
    }

def aperture(inventory):
    """
    The aperture of the array in kilometers.
    Method:find the maximum of the calculation of  distance of every possible combination of stations
    """
    lats, lngs, hgt = __coordinate_values(inventory)
    distances = []
    for i in range(len(lats)):
        for j in range(len(lats)):
            if lats[i] == lats[j]:
                continue
            distances.append(gps2DistAzimuth(lats[i],lngs[i],
                lats[j],lngs[j])[0] / 1000.0)
    return max(distances)

def find_closest_station(inventory, latitude, longitude,
                         absolute_height_in_km=0.0):
    """
    Calculates closest station to a given latitude, longitude and absolute_height_in_km
    param latitude: latitude of interest, in degrees
    type latitude: float
    param longitude: longitude of interest, in degrees
    type: float
    param absolute_height_in_km: altitude of interest in km
    type: float
    """
    min_distance = None
    min_distance_station = None

    lats, lngs, hgt = __coordinate_values(inventory)
    
    x = latitude
    y = longitude
    z = absolute_height_in_km

    for i in range(len(lats)):
        distance = np.sqrt( ((gps2DistAzimuth(lats[i], lngs[i], x, y)[0]) / 1000.0) ** 2  + ( np.abs( np.abs(z) - np.abs(hgt[i]))) ** 2 )
        if min_distance is None or distance < min_distance:
            min_distance = distance
            min_distance_station = inventory[0][i].code
    return min_distance_station

def plot_transfer_function(stream, inventory, sx=(-10, 10), sy=(-10, 10), sls=0.5, freqmin=0.1, freqmax=4.0,
                           numfreqs=10):
    """
    Plot transfer function (uses array transfer function as a function of
    slowness difference and frequency).

    :param sx: Min/Max slowness for analysis in x direction.
    :type sx: (float, float)
    :param sy: Min/Max slowness for analysis in y direction.
    :type sy: (float, float)
    :param sls: step width of slowness grid
    :type sls: float
    :param freqmin: Low corner of frequency range for array analysis
    :type freqmin: float
    :param freqmax: High corner of frequency range for array analysis
    :type freqmax: float
    :param numfreqs: number of frequency values used for computing array
     transfer function
    :type numfreqs: int
    """
    sllx, slmx = sx
    slly, slmy = sy
    sllx = kilometer2degrees(sllx)
    slmx = kilometer2degrees(slmx)
    slly = kilometer2degrees(slly)
    slmy = kilometer2degrees(slmy)
    sls = kilometer2degrees(sls)

    stepsfreq = (freqmax - freqmin) / float(numfreqs)
    transff = array_transff_freqslowness(stream, inventory, (sllx, slmx, slly, slmy),
                                               sls, freqmin, freqmax,
                                               stepsfreq)

    sllx = degrees2kilometers(sllx)
    slmx = degrees2kilometers(slmx)
    slly = degrees2kilometers(slly)
    slmy = degrees2kilometers(slmy)
    sls = degrees2kilometers(sls)

    slx = np.arange(sllx, slmx + sls, sls)
    sly = np.arange(slly, slmy + sls, sls)
    fig = plt.figure(figsize=(12, 12))
    ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])

    # ax.pcolormesh(slx, sly, transff.T)
    ax.contour(sly, slx, transff.T, 10)
    ax.set_xlabel('slowness [s/deg]')
    ax.set_ylabel('slowness [s/deg]')
    ax.set_ylim(slx[0], slx[-1])
    ax.set_xlim(sly[0], sly[-1])
    plt.show()


def plot_gcp(slat, slon, qlat, qlon, plat, plon, savefigure=None):
    
    global m
    # lon_0 is central longitude of projection, lat_0 the central latitude.
    # resolution = 'c' means use crude resolution coastlines, 'l' means low, 'h' high etc.
    # zorder is the plotting level, 0 is the lowest, 1 = one level higher ...   
    #m = Basemap(projection='nsper',lon_0=20, lat_0=25,resolution='c')
    m = Basemap(projection='kav7',lon_0=-45, resolution='c')   
    qx, qy = m(qlon, qlat)
    sx, sy = m(slon, slat)
    px, py = m(plon, plat)
    m.drawmapboundary(fill_color='#B4FFFF')
    m.fillcontinents(color='#00CC00',lake_color='#B4FFFF', zorder=0)
    #import event coordinates, with symbol (* = Star)
    m.scatter(qx, qy, 80, marker='*', color= '#004BCB', zorder=2)
    #import station coordinates, with symbol (^ = triangle)
    m.scatter(sx, sy, 80, marker='^', color='red', zorder=2)
    #import bouncepoints coord.
    m.scatter(px, py, 10, marker='d', color='yellow', zorder=2)

    m.drawcoastlines(zorder=1)
    #greatcirclepath drawing from station to event
    #Check if qlat has a length
    try:
        for i in range(len(qlat)):
            m.drawgreatcircle(qlon[i], qlat[i], slon[i], slat[i], linewidth = 1, color = 'black', zorder=1)
    except TypeError:       
        m.drawgreatcircle(qlon, qlat, slon, slat, linewidth = 1, color = 'black', zorder=1)
    # draw parallels and meridians.
    m.drawparallels(np.arange(-90.,120.,30.), zorder=1)
    m.drawmeridians(np.arange(0.,420.,60.), zorder=1)
    plt.title("")
    
    if savefigure:
        plt.savefig('plot_gcp.png', format="png", dpi=900)
    else:
        plt.show()

def attach_network_to_traces(stream, network):
	"""
	Attaches the network-code of the inventory to each trace of the stream
	"""
	for trace in stream:
		trace.meta.network = network.code

def alignon(st, inv, event, phase, maxtimewindow=None):
	"""
	Aligns traces on a given phase
	
	:param st: stream
	
	:param inv: inventory

	:param event: Eventdata

	:phase: Phase to align the traces on
	:type phase: str
	"""
	
	# Calculate depth and distance of receiver and event.
	attach_coordinates_to_traces(st, inv, event)
	depth = event.origins[0]['depth']/1000.
	
	# Prepare Array of data.
	stshift = stream2array(st)
	no_x,no_t = stshift.shape
	for j in range(no_x):
		y_dist = st[j].meta.distance
		origin = event.origins[0]['time']
		m = TauPyModel('ak135')
		time = m.get_travel_times(depth, y_dist)
		for k in range(len(time)):
			if time[k].name != phase:
				continue
			t = time[k].time
		
		phase_time = origin + t - st[j].stats.starttime
		Phase_npt = int(phase_time/st[j].stats.delta)
		if j == 0:
			tref = Phase_npt
			
		else:
			# Check for maximum Value in a timewindow of length 
			# maxtimewindow around theoretical arrival
			if maxtimewindow:
				delta = st[j].meta.delta
				tmin = Phase_npt - int( (maxtimewindow/2.)/delta )
				tmax = Phase_npt + int( (maxtimewindow/2.)/delta )
				stmax = stshift[j][Phase_npt]
				mtw_index = Phase_npt
				for k in range(tmin,tmax+1):
					if stshift[j][k] > stmax:
							stmax=stshift[j][k]
							mtw_index = k
				print("Phase delta is %i, best fit is %i" % (Phase_npt,mtw_index))
				shift_index = tref - mtw_index
			else:
				shift_index = tref - Phase_npt

			stshift[j,:] = np.roll(stshift[j,:], shift_index)
		
	return(stshift)

	
def shift_array(array, shift_value=0, y_dist=False):
	array_shift = array
	try:
		for i in range(len(array)):
			array_shift[i] = np.roll(array[i], -shift_value*y_dist[i])
	except (AttributeError, TypeError):
		for i in range(len(array)):
			array_shift[i] = np.roll(array[i], -shift_value*i)
	return(array_shift)
	

def part_stack(st, yinfo):
	ps_st=st
	return ps_st

	
	