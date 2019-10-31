## Based on rain-bypass.py from https://www.thirdeyevis.com/pi-page-3.php

import urllib.request
import json 
import RPi.GPIO as GPIO ##Import GPIO library
import os
import time
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET


## Setup GPIO I/O PIns to output mode
GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)
GPIO.setup(7, GPIO.OUT) ## This pin controls relay switch.
                        ## When ON/True, watering is disabled. Default OFF
GPIO.setup(11, GPIO.OUT) ## This pin controls a flashing red light that flashes
                        ##when data error
GPIO.setup(13, GPIO.OUT) ## This pin enables green light when watering
GPIO.setup(15, GPIO.OUT) ## This pin enables red light when watering disabled

historicalRain = [] ## Hold historical rain forecasts
config = {}         ## Hold configuration

## This funtion gets the path of this file.  When run at startup, we need full
## path to access config file.
## To run automatically at startup, change permission of this file to execute
## If using wireless for network adapter, make sure wireless settings are
## configured correctly in wlan config so wifi device is available on startup
## edit /etc/rc.local file 'sudo pico /etc/rc.local'
## add "python /home/pi/working-python/weather-json.py &" before line "exit 0"

def GetProgramDir():
   try:  ## If running from command line __file__ path is defined
      return os.path.dirname(os.path.abspath(__file__)) + "/"
   except:  ## If __file__ is undefined, we are running from idle ide
      return os.getcwd() + "/"

## Load values from config file, or create it and get values
try: ## see if config file exists
    configFile = open(GetProgramDir() + "rain-bypass.cfg","r")
    print("Config file found, loading previous values...")
    config = json.load(configFile)
    config["latValue"] = float(config["latValue"]) 
    config["longValue"] = float(config["longValue"]) 
    config["lookAhead"] = min(int(config["lookAhead"]),168)
    config["lookBehind"] = min(int(config["lookBehind"]) ,168)
    config["rainfallLimit"] = float(config["rainfallLimit"])
    config["checkIncrement"] = int(config["checkIncrement"])
    print("Finished loading previous values.")
    configFile.close()
except: ## Exception: config file does not exist, create new
    print("Config file not found, or values invalid. Creating new...")

    ## Request coordinates for request
    config["latValue"] = input("Enter Latitude (##.#### or -##.####): ")
    config["longValue"] = input("Enter Longitude (##.#### or -##.####): ")

    ## input number of hours to check for rain before and after current time
    config["lookAhead"] = min(int(input("Enter number of hours to look ahead for rain (1 to 168): ")),168)
    config["lookBehind"] = min(int(input("Enter number of hours to look back for rain (1 to 168): ")),168)

    ## input rainfall limit
    config["rainfallLimit"] = float(input("Enter rainfall amount that will disable watering, in inches/week: "))

    ## request number of checks in 24 hour period
    ## checkIncrement = int(input("Enter number of times you want to check forecast per 24-hour period " + \
    ##                           "(no more than 500, try 24, or once per hour): "))
    checkIncrement = 24 ## Must check once per hour for lookback feature to work
    config["checkIncrement"] = int(86400/checkIncrement) ## This is the wait interval between each check in seconds
    
    ## Save user input to new config file
    configFile = open(GetProgramDir() + "rain-bypass.cfg","w")
    json.dump(config, configFile)
    configFile.close()

## Show values/interval used to check weatherc
print("Checking forecast for point: " + str(config["latValue"]) + ", " + \
      str(config["longValue"]))
print("System will look for rain " + str(config["lookAhead"]) +  " hours ahead and " + \
       str(config["lookBehind"]) + " hours behind the current time.")
print("System will be disabled if rainfall rate over that period is more than \n     " + \
      str(config["rainfallLimit"]) + " inches per week.")
print("System will wait " + str(config["checkIncrement"]) + " seconds (" + \
      str(float(config["checkIncrement"]) / 60) + " minute(s) or " + \
      str(float(config["checkIncrement"]) / 3600) + " hour(s)) between checks.")

def CheckWeather():
    ## Create array for cached Quantitative Precipitation Forecast values
    oldQpf = [] 
    
    while True: ## Loop this forever
        rainForecasted = False ## Does rain exceed limit - Boolean
        
        try:
            ## Fetch XML forecast
            print("\n## Fetching forecast for " + str(config["latValue"]) + \
                  ", " + str(config["longValue"]) + " ##")
            request = "https://forecast.weather.gov/MapClick.php?lat=" + str(config["latValue"]) + \
                      "&lon=" + str(config["longValue"]) + "&FcstType=digitalDWML"
            print("Loading " + request)
            response = ET.parse(urllib.request.urlopen(request))

            ## Create array to hold new Quantitative Precipitation Forecast values
            qpf = []

            ## Parse XML into array with only precipitation values (in in/hr)
            for child in response.getroot().find('.//hourly-qpf'):
                qpf.append(float(child.text))

            print("Calculating rainfall totals...")
            if len(qpf) >= config["lookAhead"]: ## Make sure we actually gathered data
                ## Process forecast data
                rainForecasted = processForecast(qpf)
            else:
                print("Forecast too short.")
                raise ValueError

            ## Cache qpf table as fallback
            oldQpf = qpf

            GPIO.output(11,False) ## Turn off flashing red data error light if flashing, routine successful
            
        except: ## Data unavailable - either connection error, or network error
            GPIO.output(11,True) ## Turn on flashing red data error light
            if len(oldQpf)>config["lookAhead"]:
                print("Error contacting weather.gov. Using cached forecast data.")
                del oldQpf[:1] ## Remove old first entry in qpf table, since we won't be fetching a new one
                rainForecasted = processForecast(oldQpf)
            else:
                print("Error contacting weather.gov. Insufficient cached data.")
                historicalRain = [] ## Clearing historical data, since it is now inaccurate

        ## Now that we know current conditions and forecast, modify watering schedule
        ModifyWatering(rainForecasted)

        print("Checking forecast again in " + str(int(config["checkIncrement"] / 60)) + " minute(s)")
        time.sleep(config["checkIncrement"])

def processForecast(qpf):
    global historicalRain
    
    ## Add current rain amount to front of historical list and trim to 7 days
    historicalRain.insert(0,qpf[0]) 
    del historicalRain[168:]

    ## If there's not enough historical data, look ahead more
    histLen = len(historicalRain)
    lookAhead = config["lookAhead"]
    if histLen < config["lookBehind"]:
        print("Only " + str(histLen) + " hour(s) of historical data available --")
        lookAhead = min(lookAhead + config["lookBehind"] - histLen,168)
        print("     looking ahead " + str(lookAhead) + " hours.")

    ## Total rainfall ahead and behind. First value in qpf is skipped,
    ## as it is the current hour (and is counted in historicalRain)
    sampledRain = qpf[1:lookAhead + 1] + \
        historicalRain[:config["lookBehind"]]

    ## Check if rainfall exceeds rate
    rainRate = 168 * float(sum(sampledRain) / len(sampledRain))
    if (rainRate > config["rainfallLimit"]):
        print("Forecasted rainfall of " + str(round(rainRate,3)) + \
            "in/wk exceeds limit of " + \
            str(config["rainfallLimit"]) + "in/wk.")
        rainForecasted = True
    else:
        print("Forecasted rainfall of " + str(round(rainRate,3)) + \
        "in/wk is less than " + \
        str(config["rainfallLimit"]) + "in/wk limit.")
        rainForecasted = False

    return rainForecasted

def ModifyWatering(rainForecasted):

    if(rainForecasted == False):
        print("Watering enabled.")
        GPIO.output(7,False) ## Turn off relay switch, enable watering
        GPIO.output(13,True) ## Turn on green light
        GPIO.output(15,False) ## Turn off red light
    else:
        print("Watering disabled.")
        GPIO.output(7,True) ## Turn on relay switch, disable watering
        GPIO.output(13,False) ## Turn off green light
        GPIO.output(15,True) ## Turn on red light

## Init Forecast method
CheckWeather()
