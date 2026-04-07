import RPi.GPIO as GPIO
from time import sleep

GPIO.setwarnings(False)

GPIO.setmode(GPIO.BCM)

GPIO.setup(18, GPIO.OUT)

while (True):
    # Relay off
    GPIO.output(18, 1)
    print("Relay off")
    sleep(2)
    #Relay on
    GPIO.output(18, 0)
    print("Relay on")
    #sleep(5)
