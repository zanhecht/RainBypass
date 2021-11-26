#!/usr/bin/python3
# -*- coding: utf-8 -*-

# Based on rain-bypass.py from https://www.thirdeyevis.com/pi-page-3.php
#
# To set-up for the first time, after flashing Raspberry Pi OS to an SD Card:
# 1. Create a blank file on the "boot" partition of the SD card called "ssh"
# 2. Edit cmdline.txt on the boot partition to add the following to the end of the first line:
#    consoleblank=0
# 3. Edit config.txt on the boot partition and uncomment the following line:
#     dtparam=i2c_arm=on
# and add the following line below it:
#     dtparam=i2c_baudrate=1000000
# 4. Create a file on the "boot" partition called wpi-supplicant.conf with the follwing:
#
# country=XX # Your 2-digit country code (e.g. US)
# ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
# network={
#     ssid="WiFiNetworkName"
#     psk="WiFiPassword"
# }
#
# 5. Boot up the device and connect via SSH.
# 6. Run "sudo raspi-config" and enable i2c under "Interface Options"
# 7. Run the following:
#su
# sudo apt-get update
# sudo apt-get full-upgrade
# sudo apt install python3
# sudo apt install python3-pip
# sudo apt install python3-pil
# sudo apt install screen
# sudo pip3 install --upgrade setuptools
# sudo pip3 install adafruit-blinka
# sudo pip3 install adafruit-circuitpython-ssd1306
# sudo reboot
#
# 8. To run automatically at startup, change permission of this file to execute:
# sudo chmod +x rain-bypass.py
# 9.Edit cron file using "crontab -e" and add
# @reboot sleep 60 && /usr/bin/screen -d -m /home/pi/python/rain-bypass.py
#
# This will start the script 60 seconds after boot. The script output can be
# accessed via terminal or SSH by typing "screen -r". If you wish to connect
# remotely, and are using the desktop version of Raspberry Pi OS, make sure to
# disable power saving by adding the following to
# /etc/xdg/lxsession/LXDE-pi/autostart
#
# @xset s off
# @xset -dpms
#
# To do:
# # Debounce valve sensor input

class Pins:
    OpenRelay = 17      # These pins control the valve.
    CloseRelay = 4      # Set 17 on/4 off to open, reverse to close
                        # Note that 1-wire must be disabled to use GPIO 4
    DataErrLED = 11     # This pin controls a red LED that indicates when data error
    EnabledLED = 13     # This pin enables green light when watering
    DisabledLED = 15    # This pin enables red light when watering disabled
    
    ClosedSensor = 23   # Valve closed when 0
    OpenSensor = 24     # Valve open when 0
    
    BypassEnable = 0    # Force enable watering (not implemented)
    BypassDisable = 0   # Force disable watering (not implemented)

import urllib.request
import subprocess
import socket
import json 
import os
import time
from signal import signal, SIGINT
from sys import exit
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET
try:
    import RPi.GPIO as GPIO #Import GPIO library
except Exception as error:
    print(error)

# imports for piOLED screen
try:
    from board import SCL, SDA
    import busio
    import adafruit_ssd1306
    from PIL import Image, ImageDraw, ImageFont
except Exception as error:
    print(error)


config = {}             # Hold configuration
display = [None, "","","","", None, None, None] # Hold display output
firstRun = True # Always run valve on first run

def runSetup():
    global config
    global display
    display[2] = "## Rain Bypass 3.0 ##"
    print(display[2])

    # Setup screen
    try:
        i2c = busio.I2C(SCL, SDA)
        display[0] = adafruit_ssd1306.SSD1306_I2C(128, 32, i2c)
        display[0].fill(0)
        display[0].show()
        
        display[5] = Image.new('1', (display[0].width, display[0].height))
        display[6] = ImageDraw.Draw(display[5])
        display[7] = ImageFont.load_default()
        print("OLED Display found!")
    except:
        display[0] = None
    
    # Setup GPIO I/O PIns to output mode
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(Pins.OpenRelay, GPIO.OUT)
        GPIO.setup(Pins.CloseRelay, GPIO.OUT)
        GPIO.setup(Pins.DataErrLED, GPIO.OUT)
        GPIO.setup(Pins.EnabledLED, GPIO.OUT)
        GPIO.setup(Pins.DisabledLED, GPIO.OUT)
        GPIO.setup(Pins.ClosedSensor, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(Pins.OpenSensor, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    except:
        print("GPIO disabled.")
    
    # Wait for network to be active, so the clock can be set via NTP
    isConnected = False
    while(not isConnected):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("pool.ntp.org", 123))
            isConnected = True
            display[1] = "IP: %s" % getIP()
            print(display[1])
            updateOLED()
        except:
            print("Cannot reach pool.ntp.org. Waiting 30 seconds...")
            display[1] = "Not connected..."
            updateOLED()
            time.sleep(30)

    now, waitTime = int(time.time()), 0
    # Load values from config file, or create it and get values
    try: # see if config file exists
        loadConfig()
        PrintConfig()
        
        #config["time"] = (now - config["checkIncrement"]) #uncomment for debugging
        elapsedTime = int(now) - config["time"]
        waitTime = config["checkIncrement"] - (elapsedTime % config["checkIncrement"])
        
        ModifyWatering(config["rainForecasted"])
        
        timeLeft = now + waitTime - int(time.time())
        display[4] = "Waiting %i:%02i mins" % ((timeLeft/60), (timeLeft%60))
        print(display[4])
    except Exception as configError: # Exception: config file does not exist, create new
        print("Error loading value from config file: %s" % configError)
        display[3] = "INVALID CONFIG"
        display[4] = "RUN SETUP"
        updateOLED()
        buildConfig()
        display[3] = ""
        display[4] = ""
        
    while int(time.time()) < (now + waitTime):
        updateOLED()
        time.sleep(1)
        timeLeft = now + waitTime - int(time.time())
        display[4] = "Waiting %i:%02i mins" % ((timeLeft/60), (timeLeft%60))
    
    display[4] = ""
    updateOLED()

def PrintConfig():
    # Show values/interval used to check weather
    print("Checking forecast for point: %s, %s" % (config['latValue'], config['longValue']))
    print("System will look for rain %s hours ahead and %s hours behind the current time."
        % (config['lookAhead'], config['lookBehind']))
    print("System will be disabled if rainfall rate over that period is more than")
    print("    %s inches per week." % config['rainfallLimit'])
    print("System will wait %s seconds (%.1f minute(s) or %.1f hour(s)) between checks." %
        (config['checkIncrement'], (float(config['checkIncrement']) / 60),
        (float(config['checkIncrement']) / 3600)) )
    print("Valve has position sensor: %s. Always attempt to move valve: %s." % (config["valveHasSensor"], config["forceValve"]) )
    
def CheckWeather():
    global config
    global display
    global firstRun
    
    display[4] = "Starting..."
    updateOLED()
    
    while True: # Loop this forever
        # wait until next update interval
        nextUpdate = config["time"] + config["checkIncrement"]
        if int(time.time()) > nextUpdate: 
            rainForecasted = False # Does rain exceed limit - Boolean
            
            # display[1] = "Last update: " + time.strftime('%H:%M')
            
            try:
                # Fetch XML forecast
                print("\n# Fetching forecast at %s #" % time.ctime())
                request = "https://forecast.weather.gov/MapClick.php?lat=" + str(config["latValue"]) + \
                          "&lon=" + str(config["longValue"]) + "&FcstType=digitalDWML"
                print("Loading %s ... " % request, end = '')
                
                display[4] = "Fetching forecast..."
                updateOLED()

                with urllib.request.urlopen(request) as response:
                    responseTree = ET.parse(response)
                print("Done!")

                # Create array to hold new Quantitative Precipitation Forecast values
                qpf = []

                # Parse XML into array with only precipitation values (in in/hr)
                for child in responseTree.getroot().find('.//hourly-qpf'):
                    qpf.append(float(child.text))
                    

                print("Calculating rainfall totals...")
                display[4] = "Calculating..."
                updateOLED()
                if len(qpf) >= config["lookAhead"]: # Make sure we actually gathered data
                    # Process forecast data
                    rainForecasted = processForecast(qpf)
                else:
                    print("Forecast too short.")
                    display[2] = "Forecast too short"
                    raise ValueError

                # Cache qpf table as fallback
                config["qpf"] = qpf

                # Turn off flashing red data error light if flashing, routine successful
                try:
                    GPIO.output(Pins.DataErrLED, False)
                except Exception as error:
                    print(error)
                    
                display[1] = "IP: %s" % getIP()
                updateOLED()
                
            except Exception as dataError: # Data unavailable - either connection error, or network error
                try:
                    GPIO.output(Pins.DataErrLED, True) # Turn on flashing red data error light
                except Exception as error:
                    print(error)

                print("Error contacting weather.gov. %s" % dataError)
                if len(config["qpf"])>config["lookAhead"]:
                    print(" Using cached forecast data.")
                    display[2] = "Using cached data"
                    # Remove first entry in cached qpf table, since we won't be fetching a new one
                    del config["qpf"][:1] 
                    # Process forecast data
                    rainForecasted = processForecast(config["qpf"])
                else:
                    print(" Insufficient cached data.")
                    display[2] = "Insufficient cache"
                    display[4] = ""
                    config["historicalRain"] = [] # Clearing historical data, since it is now inaccurate
                    config["time"] = int(time.time()) - config["checkIncrement"] + 60

            # Now that we know current conditions and forecast, modify watering schedule
            
            if rainForecasted != config["rainForecasted"]:
                config["rainForecasted"] = rainForecasted
                ModifyWatering(config["rainForecasted"])
            elif config["valveHasSensor"] or config["forceValve"] or firstRun:
                # Always try to move the valve if it has a sensor, force option is enabled, or first run
                ModifyWatering(config["rainForecasted"])
            else:
                if rainForecasted:
                    display[3] = "Watering DISABLED"
                else:
                    display[3] = "Watering ENABLED"
                print(display[3])

            # Store values in config file
            config["time"] = int(time.time()) # Update timestamp
            with open(getCfgFile(),"w") as configFile:
                json.dump(config, configFile)
            
            print("Checking forecast again in %i minute(s)" %
                (config["checkIncrement"] / 60))
        else: # Things to do while waiting
            timeLeft = nextUpdate - int(time.time())
            display[4] = "Next update: %i:%02i" % ((timeLeft/60), (timeLeft%60))
            time.sleep(1)

        # Update display
        updateOLED()

def getCfgFile():
    cfgName = "rain-bypass-3.cfg"
    
    try:  # If running from command line __file__ path is defined
        return os.path.dirname(os.path.abspath(__file__)) + "/" + cfgName
    except:  # If __file__ is undefined, we are running from idle ide
        return os.getcwd() + "/" + cfgName

def loadConfig():
    global config
    global display
    
    with open(getCfgFile(),"r") as configFile:
        print("Config file found, loading previous values...")
        config = json.load(configFile)

    config["latValue"] = float(config["latValue"]) 
    config["longValue"] = float(config["longValue"]) 
    config["lookAhead"] = min(int(config["lookAhead"]),168)
    config["lookBehind"] = min(int(config["lookBehind"]),168)
    config["rainfallLimit"] = float(config["rainfallLimit"])
    config["checkIncrement"] = int(config["checkIncrement"])
    config["time"] = int(config["time"])
    config["rainForecasted"] = bool(config["rainForecasted"])
    config["forceValve"] = (bool(config['forceValve'])) if ('forceValve' in config) else False
    config["valveHasSensor"] = bool(config["valveHasSensor"])
    config["qpf"]
    elapsedTime = int(time.time()) - config["time"]
    print("Finished loading previous values.")
    if config["time"] == 0: #config file was reset
        config["time"] = int(time.time()) - config["checkIncrement"]
    else:
        print("Last check was %.2f minutes ago." % (elapsedTime/60))    
        incrementsToSkip = int(elapsedTime/config["checkIncrement"])

        while incrementsToSkip > 0:
            if len(config["qpf"]) > (config["lookAhead"] + 1):
                print("Catching up...")
                # Add first value in forecast to beginning of historical data
                config["historicalRain"].insert(0,config["qpf"][0])
                # Trim end of historical data
                del config["historicalRain"][168:]
                # Delete first entry from forecast, now that it's moved
                del config["qpf"][:1]
                # One less increment to skip needed
                incrementsToSkip -= 1
            else:
                print("Insufficient cached data. Clearing stale historical data")
                config["historicalRain"], config["qpf"] = [], []
                incrementsToSkip = 0 
                config["time"] = int(time.time()) - config["checkIncrement"] + 60

def buildConfig():
    global config
    global display

    print("Config file not found, or values invalid. Creating new...")

    # Request coordinates for request
    config["latValue"] = input("Enter Latitude (#.## or -#.##): ")
    config["longValue"] = input("Enter Longitude (#.## or -#.##): ")

    # input number of hours to check for rain before and after current time
    config["lookAhead"] = min(int(input("Enter number of hours to look ahead for rain (1 to 168): ")),168)
    config["lookBehind"] = min(int(input("Enter number of hours to look back for rain (1 to 168): ")),168)

    # input rainfall limit
    config["rainfallLimit"] = float(input("Enter rainfall amount that will disable watering, in inches/week: "))
    
    # use valve position sensor
    config["valveHasSensor"] = ""
    while config["valveHasSensor"].lower() != 'y' and config["valveHasSensor"].lower() != 'n':
        config["valveHasSensor"] = str(input("Use position sensor on valve? ([y]es/[n]o) "))
    config["valveHasSensor"] = (config["valveHasSensor"] == 'y')
    config["forceValve"] = (bool(config['forceValve'])) if ('forceValve' in config) else False

    # request number of checks in 24 hour period
    # checkIncrement = int(input("Enter number of times you want to check forecast per 24-hour period " + \
    #                           "(no more than 500, try 24, or once per hour): "))
    checkIncrement = 24 # Must check once per hour for lookback feature to work
    config["checkIncrement"] = int(86400/checkIncrement) # This is the wait interval between each check in seconds

    # Create arrays for cached and historical Quantitative Precipitation Forecast values
    config["qpf"] = []
    config["historicalRain"] = []

    # Create forecase placeholder
    config["rainForecasted"] = False
    
    # Save user input to new config file
    config["time"] = int(time.time()) - config["checkIncrement"] # Update timestamp
    with open(getCfgFile(),"w") as configFile:
        json.dump(config, configFile)
        
    PrintConfig()

def processForecast(qpf):
    global config
    global display
    
    # Add current rain amount to front of historical list and trim to 7 days
    config["historicalRain"].insert(0,qpf[0]) 
    del config["historicalRain"][168:]
    
    # If there's not enough historical data, look ahead more
    histLen = len(config["historicalRain"])
    lookAhead = config["lookAhead"]
    if histLen < config["lookBehind"]:
        print("Only %s hour(s) of historical data available --" % histLen)
        lookAhead = min(lookAhead + config["lookBehind"] - histLen,168)
        print("    looking ahead %s hours." % lookAhead)

    # Total rainfall ahead and behind. First value in qpf is skipped,
    # as it is the current hour (and is counted in config["historicalRain"])
    sampledRain = qpf[1:lookAhead + 1] + \
        config["historicalRain"][:config["lookBehind"]]

    # Check if rainfall exceeds rate
    rainRate = 168 * float(sum(sampledRain) / len(sampledRain))
    if (rainRate > config["rainfallLimit"]):
        print("Forecasted rainfall of %s in/wk exceeds limit of %s in/wk." %
              (round(rainRate,3), config['rainfallLimit']))
        rainForecasted = True
    else:
        print("Forecasted rainfall of %s in/wk is less than %s in/wk limit." %
            (round(rainRate,3), config['rainfallLimit']))
        rainForecasted = False
    
    display[2] = "%.1f in/wk rain fcst" % rainRate
    updateOLED()
    
    return rainForecasted

def ModifyWatering(rainForecasted):
    global display
    global firstRun
    
    firstRun = False
    
    oldLine4 = display[4]
    
    if(rainForecasted == False):
        display[3] = "Watering ENABLED"
        display[4] = "Opening valve..."
        print("%s. %s" % (display[3], display[4]))
        updateOLED()
        try:
            GPIO.output(Pins.OpenRelay, True)  # Open valve to...
            GPIO.output(Pins.CloseRelay, False)  # enable watering
            GPIO.output(Pins.EnabledLED, True)  # Turn on green light
            GPIO.output(Pins.DisabledLED, False) # Turn off red light
            now = int(time.time())
            while (int(time.time()) < now + 30) and GPIO.input(Pins.OpenSensor):
                # wait for valve to open or 30 seconds to elapse
                pass
            if GPIO.input(Pins.OpenSensor):
                if config["valveHasSensor"]:
                    display[4] = "Valve opening FAILED"
                    print(display[4])
                    updateOLED()
                else:
                    display[4] = oldLine4
                    updateOLED()
            else:
                display[4] = oldLine4
                updateOLED()
        except Exception as error:
            print(error)
    else:
        display[3] = "Watering DISABLED"
        display[4] = "Closing valve..."
        print("%s. %s" % (display[3], display[4]))
        updateOLED()
        try:
            GPIO.output(Pins.OpenRelay, False) # Close valve to...
            GPIO.output(Pins.CloseRelay, True)   # disable watering
            GPIO.output(Pins.EnabledLED, False) # Turn off green light
            GPIO.output(Pins.DisabledLED, True)  # Turn on red light
            now = int(time.time())
            while (int(time.time()) < now + 30) and GPIO.input(Pins.ClosedSensor):
                # wait for valve to open or 30 seconds to elapse
                pass
            if GPIO.input(Pins.ClosedSensor):
                if config["valveHasSensor"]:
                    display[4] = "Valve closing FAILED"
                    print(display[4])
                    updateOLED()
            else:
                display[4] = oldLine4
                updateOLED()
        except Exception as error:
            print(error)

    try:
        GPIO.output(Pins.OpenRelay, False) # Close both relays...
        GPIO.output(Pins.CloseRelay, False)  # before exiting
    except Exception as error:
        print(error)

def getIP():
    cmd = "hostname -I | cut -d\' \' -f1"
    IP = subprocess.check_output(cmd, shell=True).decode("utf-8")
    return IP


def updateOLED():
    global display
    if display[0]:
        # display[0] is disp object
        # display[1] - [4] are lines 1-4 of text
        # display[5] is the image object
        # display[6] is the draw object
        # display[7] is the font
        
        display[6].rectangle((0, 0, display[0].width, display[0].height), outline=0, fill=0)
        display[6].text((0, -2), display[1], font=display[7], fill=255)
        display[6].text((0,  6), display[2], font=display[7], fill=255)
        display[6].text((0, 14), display[3], font=display[7], fill=255)
        display[6].text((0, 22), display[4], font=display[7], fill=255)
        display[0].image(display[5])
        display[0].show()

def shutdown(signum, frame):
    global config
    
    print('SIGINT or CTRL-C detected. Exiting.')
    with open(getCfgFile(),"w") as configFile:
        json.dump(config, configFile)
    GPIO.output(Pins.OpenRelay, False) 
    GPIO.output(Pins.CloseRelay, False) 
    GPIO.output(Pins.EnabledLED, False) 
    GPIO.output(Pins.DisabledLED, False) 
    display[1], display[2], display[3], display[4] = "", "", "", ""
    updateOLED()
    exit()

# Main program loop
if __name__ == '__main__':
    # Tell Python to run the shutdown() function when SIGINT (CTRL-C) is recieved
    signal(SIGINT, shutdown)

    # Run setup
    runSetup()
    # Init Forecast method
    CheckWeather()
