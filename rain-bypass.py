import urllib.request
import urllib.parse
import datetime
import json
import time
import RPi.GPIO as GPIO ##Import GPIO library
import os

## Setup GPIO I/O PIns to output mode
GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)
GPIO.setup(7, GPIO.OUT) ## This pin controls relay switch. When ON/True, watering is disabled. Default OFF
GPIO.setup(11, GPIO.OUT) ## This pin controls a flashing red light that flashes when data error
GPIO.setup(13, GPIO.OUT) ## This pin enables green light when watering
GPIO.setup(15, GPIO.OUT) ## This pin enables red light when watering disabled

wundergroundKey = "xxxxxxxxxxxxxxxx" ## Enter your Wunderground key acquired by joining weather underground api program
lastRain = 0 ## Hold epoch of last rain - float
checkIncrement = 0  ## Amount of time between weather.com forecast requests - integer
daysDisabled = 0 ## Days to disable systems prior to and after rain - integer
zipCode = 0 ## Zip code for weather request - integer
rainForecasted = False ## Is rain forecasted within daysDisabled forecast range - Boolean, global

## Define conditions that will disable watering.  This includes Light/Heavy prefix on any of the following conditions
## Weather.com Condition Phrases: http://www.wunderground.com/weather/api/d/docs?d=resources/phrase-glossary
possibleConditions = ["Rain",
                      "Rain Showers",
                      "Thunderstorm",
                      "Thunderstorms and Rain",
                      ]

## Prefix each possible condition with 'Heavy' and 'Light', since these are possible conditions
for x in possibleConditions[:]: ## Use slice notation to iterate loop inside slice copy of list
    possibleConditions.insert(0,'Light ' + x)
    possibleConditions.insert(0,'Heavy ' + x)

##This funtion gets the path of this file.  When run at startup, we need full path to access config file
##To run this file automatically at startup, change permission of this file to execute
##If using wireless for network adapter, make sure wireless settings are configured correctly in wlan config so wifi device is available on startup
##edit /etc/rc.local file 'sudo pico /etc/rc.local'
##add "python /home/pi/working-python/weather-json.py &" before line "exit 0"
def GetProgramDir():
   try:  ## If running from command line __file__ path is defined
      return os.path.dirname(os.path.abspath(__file__)) + "/"
   except:  ## If __file__ is undefined, we are running from idle ide, which doesn't use this var
      return os.getcwd() + "/"

## Load values from config file, or create it and get values
try: ## see if config file exists
    configFile = open(GetProgramDir() + "rain-bypass.cfg","r")  ## Attempt to open existing cfg file
    print("Config file found, loading previous values...")
    zipCode = int(configFile.readline()) ## Convert zip to int to remove unicode formatting, store in zipCode
    daysDisabled = int(configFile.readline()) ## Convert second line to int and store in daysDisabled var
    checkIncrement = int(configFile.readline()) ## Conver third line to int and store in checkIncrement var
    configFile.close()
except: ## Exception: config file does not exist, create new
    print("Config file not found, creating new...")

    ## Request zip code for request
    latValue = int(input("Enter Latitude (##.#### or -##.####): "))
    longValue = int(input("Enter Longitude (##.#### or -##.####): "))
    latLong = str(latValue) + "," + str(longValue)

    ## input number of days system will be disabled prior to rain, and after rain
    daysDisabled = int(raw_input("Enter number of days to disable system prior/after rain (between 1 and 9): "))

    ## request number of checks in 24 hour period
    checkIncrement = int(raw_input("Enter number of times you want to check forecast per 24-hour period (no more than 500, try 24, or once per hour): "))
    checkIncrement = 86400/checkIncrement ## This is the wait interval between each check in seconds
    
    ## Save user input to new config file
    configFile = open(GetProgramDir() + "rain-bypass.cfg","w")
    configFile.write(str(zipCode) + "\n" + str(daysDisabled) + "\n" + str(checkIncrement) + "\n") ## Write each item to new line
    configFile.close()

## Show values/interval used to check weatherc
print("Checking forecast for point: " + str(latLong))
print("System will be disabled for " + str(daysDisabled) + " days prior to and after rain")
print("System will wait " + str(checkIncrement) + " seconds between checks")
print("     or " + str(float(checkIncrement) / 60) + " minute(s) between checks")
print("     or " + str(float(checkIncrement) / 3600) + " hour(s) between checks")

def CheckWeather():

    ## This function will modify the following variables in the main scope
    global rainForecasted
    global lastRain
    
    while True: ## Loop this forever
        try:
            ##Request Weather Data
            request = "https://api.weather.gov/points/" + urllib.parse.quote(latLong)
            response = urllib.request.urlopen(request)
            jsonData = json.load(response)
    
            #request = jsonData["properties"]["forecast"]
            request = jsonData["properties"]["forecastGridData"]
            response = urllib.request.urlopen(request)
            jsonData = json.load(response)
            #Compare timestamp with datetime.datetime.fromisoformat("2019-05-09T23:07:32+00:00") - datetime.datetime.now(datetime.timezone.utc)

            ## Create array to hold forecast values
            dateArray = []

            ## Parse XML into array with only pretty date, epoch, and conditions forecast
            jsonData = json.load(response)
            for x in jsonData['forecast']['simpleforecast']['forecastday']:
                dateArray.append([x['date']['pretty'],x['date']['epoch'],x['conditions']])

            print("\nCurrent Forecast for current day, plus next 9 is:")
            for x in dateArray:
                print x[0] + ", " + x[1] + ", " + x[2]

            ##Check current day for rain
            print "\n### START Checking if raining TODAY ###"
            if(CheckCondition(dateArray[0][2])): ## If is raining today
                lastRain = float(dateArray[0][1]) ## Save current rain forecast as last rain globally
                print "It will rain today. Storing current epoch as 'last rain': " + str(lastRain)
            else:
                print "No rain today"
            print "### END Checking if raining now ###\n"

            ##Check if rain is forecast within current range
            print "### START Checking for rain in forecast range ###"
            for x in range(1, daysDisabled+1):
                print "Checking " + dateArray[x][0] + " for rain conditions:"
                if(CheckCondition(dateArray[x][2])):
                   print("Rain has been forecast. Disabling watering")
                   rainForecasted = True ##Set global variable outside function scope
                   break
                else:
                   print("No rain found for current day. Watering may commence")
                   rainForecasted = False ##Set global variable outside function scope
            print "### END Checking if rain in forecast ###\n"

            ## Now that we know current conditions and forecast, modify watering schedule
            ModifyWatering()

            GPIO.output(11,False) ## Turn off flashing red data error light if flashing, routine successful
            print "Checking forecast again in " + str(checkIncrement / 60) + " minute(s)"
            time.sleep(checkIncrement)
            
        except: ## Data unavailable - either connection error, or network error
            GPIO.output(11,True) ## Turn on flashing red data error light
            print "Error contacting weather.com. Trying again in " + str(checkIncrement / 60) + " minute(s)"
            time.sleep(checkIncrement)  ## Reattempt connection in 1 increment

def CheckCondition(value):
    for x in possibleConditions:
        if value == x:
            print 'Rain condition found';
            return True

def ModifyWatering():
    print "\nLast rain from forecast timestamp: " + str(lastRain)
    print "Current Time: " + str(time.time())
    print "Days since last rain: " + str((time.time() - lastRain)/86400 )
    print "Seconds since last rain: " + str(time.time() - lastRain)
    print "Days disabled in seconds: " + str(daysDisabled * 86400)
    print "Has NOT rained within daysDisabled range: " + str(time.time() - lastRain >= daysDisabled * 86400)

    if(rainForecasted == False and time.time() - lastRain >= daysDisabled * 86400):
        print "Hasn't rained in a while, and not expected to rain. Watering enabled."
        GPIO.output(7,False) ## Turn off relay switch, enable watering
        GPIO.output(13,True) ## Turn on green light
        GPIO.output(15,False) ## Turn off red light
    else:
        GPIO.output(7,True) ## Turn on relay switch, disable watering
        GPIO.output(13,False) ## Turn off green light
        GPIO.output(15,True) ## Turn on red light
        if(rainForecasted):
            print "Rain is forecasted, or raining today. Watering Disabled"
        else:
            print "Rain not in forecast, but it has rained recently. Watering Disabled"
        
## Init Forecast method
CheckWeather()
