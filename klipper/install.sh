#!/bin/bash

HOME_DIR="${HOME}/klipper"

if [  -d "$1" ] ; then
    
	echo "$1"
	HOME_DIR=""$1"/klipper"
fi

BDDIR="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"


if [ ! -d "$HOME_DIR" ] ; then
    echo ""
    echo "path error doesn't exist in "$HOME_DIR""
    echo ""
    echo "bdpressure sensor path: "$BDDIR""
    echo ""
    echo "usage example:./install.sh /home/pi  or ./install.sh /home/mks "
    echo "Error!!"
    exit 1
fi

echo "klipper path:  "$HOME_DIR""
echo "bdpressure sensor path: "$BDDIR""
echo ""

echo "linking bdpressure.py to klippy."

if [ -e "${HOME_DIR}/klippy/extras/bdpressure.py" ]; then
    rm "${HOME_DIR}/klippy/extras/bdpressure.py"
fi
ln -s "${BDDIR}/bdpressure.py" "${HOME_DIR}/klippy/extras/bdpressure.py"


if ! grep -q "klippy/extras/bdpressure.py" "${HOME_DIR}/.git/info/exclude"; then
    echo "klippy/extras/bdpressure.py" >> "${HOME_DIR}/.git/info/exclude"
fi



echo ""
echo "Install bdpressure sensor successful "
echo ""
echo "happy printing!"
