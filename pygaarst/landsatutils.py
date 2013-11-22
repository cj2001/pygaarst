# coding: utf-8
"""
pygaarst.landsatutils

Utility functions for parsing Landsat metadata files

Created by Chris Waigl on 2013-11-13.
Implemented:
  - Landsat
"""

# ================================
# = Landsat parameters for bands =
# ================================

LANDSATBANDS = {
    'L4': ['1', '2', '3', '4', '5', '6', '7'],
    'L5': ['1', '2', '3', '4', '5', '6', '7'],
    'L7': ['1', '2', '3', '4', '5', '6L', '6H', '7'],
    'L8': ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11']
}

def get_bands(spacecraftid):
    try:
        return LANDSATBANDS[spacecraftid]
    except KeyError:
        logging.error("Band labels are available for TM, ETM+ and OLI/TIR sensors on %s." % ', '.join(LANDSATBANDS.keys()))

# ==================================================================
# = Landsat metadata parsing                                       =
# 
# The metadata file looks like this:
# GROUP = L1_METADATA_FILE
#   GROUP = METADATA_FILE_INFO
#     ORIGIN = "Image courtesy of the U.S. Geological Survey"
#     REQUEST_ID = "0501306252996_00005"
#     ...
#     STATION_ID = "LGN"
#     PROCESSING_SOFTWARE_VERSION = "LPGS_2.2.2"
#  END_GROUP = METADATA_FILE_INFO
#  GROUP = PRODUCT_METADATA
#     DATA_TYPE = "L1T"
#     ...
#  END_GROUP = PRODUCT_METADATA
#  END_GROUP = L1_METADATA_FILE
#  END
# ==================================================================

import os.path, glob
import datetime
import re
import math
import numpy as np
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('pygaarst.raster')

# Landsat metadata files end in _MTL.txt or _MTL.TXT
METAPATTERN = "*_MTL*"

# Elements from the file format used for parsing
GRPSTART = "GROUP = "
GRPEND = "END_GROUP = "
ASSIGNCHAR = " = "
FINAL = "END"

# A simple state machine is used to parse the file. There are 5 states (0 to 4):
STATUSCODE = ["begin", "enter metadata group", "add metadata item", "leave metadata group", "end"]

# A custom exception for this module
class LandsatMTLParseError(Exception):
    pass

# Help functions to identify the current line and extract information
def _islinetype(line, testchar):
    return line.strip().startswith(testchar)

def _isassignment(line):
    return ASSIGNCHAR in line

def _isfinal(line):
    return line.strip() == FINAL

def _getgroupname(line):
    return line.strip().split(GRPSTART)[-1]

def _getendgroupname(line):
    return line.strip().split(GRPEND)[-1]

def _getmetadataitem(line):
    return line.strip().split(ASSIGNCHAR)

# After reading a line, what state we're in depends on the line and the state before reading
def _checkstatus(status, line):
    """returns state/status after reading the next line"""
    newstatus = 0
    if status == 0:
        # begin --> enter metadata group OR end
        if _islinetype(line, GRPSTART):
            newstatus = 1
        elif _isfinal(line):
            newstatus = 4
    elif status == 1:
        # enter metadata group --> enter metadata group OR add metadata item OR leave metadata group
        if _islinetype(line, GRPSTART):
            newstatus = 1
        elif _islinetype(line, GRPEND):
            newstatus = 3
        elif _isassignment(line):
            # test AFTER start and end, as both are also assignments
            newstatus = 2
    elif status == 2:
        if _islinetype(line, GRPEND):
            newstatus = 3
        elif _isassignment(line):
            # test AFTER start and end, as both are also assignments
            newstatus = 2
    elif status == 3:
        if _islinetype(line, GRPSTART):
            newstatus = 1
        elif _islinetype(line, GRPEND):
            newstatus = 3
        elif _isfinal(line):
            newstatus = 4        
    if newstatus != 0:
        return newstatus
    elif status != 4:
        raise LandsatMTLParseError("Cannot parse the following line after status '%s':\n%s" % (STATUSCODE[status], line))

# Function to execute when reading a line in a given state
def _transstat(status, grouppath, dictpath, line):
    if status == 0:
        raise LandsatMTLParseError("Status should not be '%s' after reading line:\n%s" % (STATUSCODE[status], line))
    elif status == 1:
        currentdict = dictpath[-1]
        currentgroup = _getgroupname(line)
        grouppath.append(currentgroup)
        currentdict[currentgroup] = {}
        dictpath.append(currentdict[currentgroup])
    elif status == 2:
        currentdict = dictpath[-1]
        newkey, newval = _getmetadataitem(line)
        currentdict[newkey] = _postprocess(newval)
    elif status == 3:
        oldgroup = _getendgroupname(line)
        if oldgroup != grouppath[-1]:
            raise LandsatMTLParseError("Reached line '%s' while reading group '%s'." % (line.strip(), grouppath[-1]))
        del grouppath[-1]
        del dictpath[-1]
        try:
            currentgroup = grouppath[-1]
        except IndexError:
            currentgroup = None
    elif status == 4:
        if grouppath:
            raise LandsatMTLParseError("Reached end before end of group '%s'" % grouppath[-1])
    return grouppath, dictpath

# Identifying data type of a metadata item and 
def _postprocess(valuestr):
    """Takes value as string, returns string, integer, float, date, datetime, or time"""
    intpattern = re.compile(r'^\-?\d+$')
    floatpattern = re.compile(r'^\-?\d+\.\d+(E[+-]?\d\d+)?$')
    datedtpattern = '%Y-%m-%d'
    datedttimepattern = '%Y-%m-%dT%H:%M:%SZ'
    timedtpattern = '%H:%M:%S.%f'
    timepattern = re.compile(r'^\d{2}:\d{2}:\d{2}(\.\d{6})?')
    if valuestr.startswith('"') and valuestr.endswith('"'):
        # it's a string
        return valuestr[1:-1]
    elif re.match(intpattern, valuestr):
        # it's an integer
        return int(valuestr)
    elif re.match(floatpattern, valuestr):
        # floating point number
        return float(valuestr)
    # now let's try the datetime objects ; throws exception if it doesn't match
    try:
        return datetime.datetime.strptime(valuestr, datedtpattern).date()
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(valuestr, datedttimepattern)
    except ValueError:
        pass
    # time parsing is complicated: Python's datetime module only accepts 
    # fractions of a second only up to 6 digits
    m = re.match(timepattern, valuestr)
    if m:
        test = m.group(0)
        try:
            return datetime.datetime.strptime(test, timedtpattern).time()
        except ValueError:
            pass
    
    # If we get here, we still haven't returned anything.
    logging.info("The value %s couldn't be parsed as int, float, date, time, datetime. Returning it as string." % valuestr)
    return valuestr

def parsemeta(metadataloc):
    """metadataloc: a filename or a directory. Returns metadata dictionary"""

    # filename or directory? if several fit, use first one and warn
    if os.path.isdir(metadataloc):
        metalist = glob.glob(os.path.join(metadataloc, METAPATTERN))
        if not metalist:
            raise LandsatMTLParseError("No files matching metadata file pattern in directory %s." % metadataloc)
        elif len(metalist) > 0:
            metadatafn = metalist[0]
            if len(metalist) > 1:
                logging.warning("More than one file in directory match metadata file pattern. Using %s." % metadatafn)
    elif os.path.isfile(metadataloc):
        metadatafn = metadataloc
        logging.info("Using file %s." % metadatafn)
    else:
        raise LandsatMTLParseError("File location %s is unavailable or doesn't contain a suitable metadata file." % metadataloc)
    
    # Reading file line by line and inserting data into metadata dictionary 
    status = 0
    metadata = {}
    grouppath = []
    dictpath = [metadata]
    with open(metadatafn, 'rU') as fn:
        for line in fn:
            if status == 4:
                # we reached the end in the previous iteration, but are still reading lines
                logging.warning("Found end before finishing file parsing.")
            status = _checkstatus(status, line)
            grouppath, dictpath = _transstat(status, grouppath, dictpath, line)
    return metadata

def lskeyselect(isnew, keystr):
    """
    Translates key strings from old to new metadata format, dependent on self.newmetaformat 
    (Boolean). See http://landsat.usgs.gov/Landsat_Metadata_Changes.php for changes in August 2012.\
        Only implemented for keys that are used in this module.
    """
    new2old = {
        'DATE_ACQUIRED': 'ACQUISITION_DATE'
    }
    if not isnew:
        try:
            return new2old[keystr]
        except KeyError:
            logging.warning("Key %s might not be valid for old-style metadata files." % keystr)
    else:
        return keystr



# =====================================================================
# = Landat Thermal Bands Radiance to Brightness Temperature Conversion =
# 
# See Chander, G., Markham, B. L., Helder, D.L. (2009): 
# Summary of current radiometric calibration coefficients for Landsat 
# MSS, TM, ETM+, and EO-1 ALI sensors, Remote Sensing of Environment, 893-903. 
# http://dx.doi.org/10.1016/j.rse.2009.01.007
#
# K1 in W/(m^s sr μm). K2 in K
# See also http://landsathandbook.gsfc.nasa.gov/data_prod/prog_sect11_3.html
# =====================================================================

# Constants for Landsat 4, 5, 7
# NOTE: For Landat 8/LCM the K1 and K2 constants are provided in the metadata

K1_L4_TM = 671.62
K2_L4_TM = 1284.30
K1_L5_TM = 607.76
K2_L5_TM = 1260.56
K1_L7_EMTplus = 666.09
K2_L7_EMTplus = 1282.71
KtoC = 273.15

def getKconstants(spacecraftid):
    if spacecraftid == 'L4':
        return K1_L4_TM, K2_L4_TM
    elif spacecraftid == 'L5':
        return K1_L5_TM, K2_L5_TM
    elif spacecraftid == 'L7':
        return K1_L7_EMTplus, K2_L7_EMTplus
    else:
        logging.warning('SpacecraftID not in L4, L5, L7. Check metadata or spacecraftID. Or both.')
        

def gainbias(lmax, lmin, qcalmax, qcalmin):
    gain = (lmax - lmin)/(qcalmax - qcalmin)
    bias = (qcalmax*lmin - qcalmin*lmax)/(qcalmax - qcalmin)
    return gain, bias

def dn2rad(data, gain, bias):
    return data * gain + bias

def rad2kelvin(data, k1, k2):
    return np.divide(k2, np.log(np.divide(k1, data) + 1))
    
def rad2celsius(data, k1, k2):
    return rad2kelvin(data, k1, k2) - KtoC

    