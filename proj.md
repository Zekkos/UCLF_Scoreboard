This project is a scoreboard for a linefollower competition.

Language should be python, qtpy should be used for windows.
The program will interface via usb serial to an MCU that will give a "start", "end and time elapsed" for use by the scoreboard.

Colour scheme should be darkmode gruvbox

## Input window
- Handle interfacing via usb serial
- The ability to save and load results
- Results should be automatically saved as it is being used
- Manual input of a result for a team (but can be automatically filled out by the messages via usb serial)
    - This should require a submit button before results are submitted. 
- Should safely handle the usb connection disappearing and coming back
- Should be able to edit existing results and times, all saved times should be editable 
- A serial output from the usb serial interface should be shown
- A reset button to send a reset command over usb serial
    - This reset button also stops the scoreboard counting up

## Scoreboard window
The scoreboard will show teams in order from fastest to slowest. Each row in the scoreboard will have the rank, team number, and all of their race times (up to 3 times).
Each time the results in the input window are updated, they should update here.
There should be a large timer counting up when the "start" message is recieved, and should stop when "end and time elapsed" is recieved.
The timer number being displayed should be the number that comes "end and time elapsed" message.

## USB Serial interface
### Received
- Start message ASCII
    - "s\n"
- End and time elapsed message
    - "e,10.10\n"
    - The time elapsed will only go to 10ms resolution, but can have an arbtrially large number of seconds. Though its unlikey the number of seconds to be greater than 200s 
- Reset
    - "r\n"
    - The reset message means the time tracking system has been manually reset by someone pressing the reset button on the gates.
### Transmit
- Reset
    - "r\n"
    - This reset command can be sent to the gates to reset them to stop tracking time.
