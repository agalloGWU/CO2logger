#!/usr/bin/env python

# FlyOxide
# Based on AKLogger.py
# for Alaska 2016.  BAsed on Li_Logger from April

# TODO: read host name to use as job name (or maybe a config file with this info?
# TODO: consider switching from Prometheus to InFlux (because Prometheus isn't a long term data store
#       nor does it have a good database-like interface)


from argparse import ArgumentParser
from datetime import datetime
from time import sleep, strftime
from sys import exit
from adafruit_bme280 import basic as adafruit_bme280
import board
from meteocalc import heat_index
import serial

parser = ArgumentParser(description="Read and log data from K30 & BME280 sensors")

parser.add_argument("-c", "--console", dest='console', action='store_false',
                    help="Do NOT print data to console while running (default=echo to console)")
parser.add_argument("-f", "--file", dest='write_to_file', action='store_false',
                    help="Do NOT save data to file (default=write to file)")
parser.add_argument('-p', '--prom',
                    default='disable',
                    choices=['push', 'pull', 'disable'],
                    help='Sets the mode of data export using Prometheus (default: %(default)s)')

args = parser.parse_args()

console = args.console
write_to_file = args.write_to_file
prom_mode = args.prom


# try to import Prometheus
prom_present = False
if prom_mode == 'pull':
    try:
        # this is the default more for prometheus- for the server to scrape (or pull) data from the node
        # via http
        # configured it:
        # start the listener for prometheus metrics on port 9320
        # will be available at http://addr:9320/metrics
        from prometheus_client import Gauge, start_http_server
        start_http_server(9320)
        prom_present = True
        # define prometheus metrics
        # prepend var name with 'p' to differentiate it from original variables in the code
        pCO2 = Gauge('CO2_ppm', 'Carbon Dioxide in parts per million')
        pTemp = Gauge('Temp_C', 'Temperature in C')
        pPres = Gauge('pressure_mbar', 'Barometric pressure in millibars')
        pHumidity = Gauge('humidity_perc', 'Humidity, percent')
        pHeat_index = Gauge('heat_index', 'Heat index in F')
    except ImportError:
        prom_present = False
        print("Prometheus client not found....not enabling feature")

if prom_mode == 'push':
    try:
        from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
        registry = CollectorRegistry()
        pCO2 = Gauge('CO2_ppm', 'Carbon Dioxide in parts per million', registry=registry)
        pTemp = Gauge('Temp_C', 'Temperature in C', registry=registry)
        pPres = Gauge('pressure_mbar', 'Barometric pressure in millibars', registry=registry)
        pHumidity = Gauge('humidity_perc', 'Humidity, percent', registry=registry)
        pHeat_index = Gauge('heat_index', 'Heat index in F', registry=registry)
        prometheus_host = '128.164.12.3'
        prometheus_port = 9091
        prometheus_job = 'sensor99'
        prom_present = True
    except ImportError:
        prom_present = False
        print("Prometheus client not found....not enabling feature")


# do some hardware stuff.  it's easier if this is global
# the serial error/traceback reporting isn't playing nice with try/except
# it appears that SerialException and PermissionError should be the exceptions to match
# but that doesn't work, so we need a broad exception ('except Exception' instead of 'except PermissionError')
try:
    ser = serial.Serial("/dev/ttyAMA0", 9600, timeout=1)
except Exception as e:
    exit("Unable to open serial port.\nMaybe you should be root?\n{}".format(e))
# Create sensor object, using the board's default I2C bus.
i2c = board.I2C()  # uses board.SCL and board.SDA
bme280 = adafruit_bme280.Adafruit_BME280_I2C(i2c)


def dtstamp():
    t = datetime.now()
    return "[" + str(t.year) + "-" + str(t.month).zfill(2) + "-" + \
           str(t.day).zfill(2) + " " + str(t.hour).zfill(2) + ":" + \
           str(t.minute).zfill(2) + ":" + str(t.second).zfill(2) + "] "


def readCO2():
    # original command string in documentation.  new pyserial module doesn't like strings,
    # cmd="\xFE\x44\x00\x08\x02\x9F\x25"
    # so we need to switch to a byte array
    cmd = bytearray()
    cmd.append(0xFE)
    cmd.append(0x44)
    cmd.append(0x00)
    cmd.append(0x08)
    cmd.append(0x02)
    cmd.append(0x9F)
    cmd.append(0x25)
    ser.write(cmd)
    sleep(0.5)
    result = ser.read(7)
    if len(result) != 7:
        # we probably need to handle this better
        # eg: if we don't get valid data, make a note, but don't die
        # maybe remove ser.close()?
        # maybe remember last value, or a dummy value so we know data is stale?
        print('Result is not 7 bytes: {}'.format(result))
        ser.close()
    co2 = result[3]*255 + result[4]
    return co2


def read_BME280():
    degrees = bme280.temperature
    pascals = bme280.pressure
    humidity = bme280.humidity
    return degrees, pascals, humidity


def loopForever():
    minutes_to_average = 0.5
    now = strftime("%Y-%m-%d-%H:%M")
    while True:
        sumCO2 = 0
        sumTemp = 0
        sumPres = 0
        sumhum = 0
        # This should average for xx  minutes and then report
        for i in range(0,int(12*minutes_to_average)):
            sumCO2 = sumCO2 + readCO2()
            degrees, pascals, humidity = read_BME280()
            sumTemp = sumTemp + degrees
            sumPres = sumPres + pascals
            sumhum = sumhum + humidity
            sleep(4.9)
        CO2 = sumCO2 / (12.0 * minutes_to_average)
        Temp = sumTemp / (12.0 * minutes_to_average)
        Pres = sumPres / (12.0 * minutes_to_average)
        Humidity = sumhum / (12.0 * minutes_to_average)
        TempF = (Temp * 9 / 5) + 32
        HeatIndex = float((heat_index(TempF, Humidity)))
        if console:
            # echo to console
            timestamp = dtstamp()
            print(timestamp)
            print("CO2: {:.2f}".format(CO2))
            print("Temp: {:.2f}".format(TempF))
            print("Heat Index: {:.2f}".format(HeatIndex))
            print("Pres: {:.2f}".format(Pres))
            print("Humidity: {:.2f}".format(Humidity))
            print("\n")
        if prom_present:
            # update prometheus metrics
            # if we're operating in pull mode, we don't need to do anything- these metrics will be exposed
            # by the HTTP server
            pCO2.set(CO2)
            pTemp.set(Temp)
            pPres.set(Pres)
            pHumidity.set(Humidity)
            pHeat_index.set(HeatIndex)
            # if we're operating in push mode, we need to do more work
            if prom_mode == 'push':
                push_to_gateway(f"{prometheus_host}:{prometheus_port}", job="sensor99", registry=registry)
        if write_to_file:
            filename = './data/' + now + '.txt'
            with open(filename, 'a') as f:
                f.write(timestamp)
                # stringtowrite = "%.1f %.2f %.1f %.1f \n" % (CO2, Temp, Pres, Humidity)
                stringtowrite = "CO2: {:.2f}, Temp: {:.2f}, Pres: {:.2f}, Humid: {:.2f}\n".format(CO2, TempF, Pres,
                                                                                                Humidity)
                f.write(stringtowrite)


def main():
    try:
        loopForever()
    except KeyboardInterrupt:
        exit("Goodbye")


if __name__ == "__main__":
    main()

