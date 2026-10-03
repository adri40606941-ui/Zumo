#!/bin/bash
# Banner ZUMO en colores. Se usa en el instalador y como mensaje de bienvenida (MOTD) de la VPS.
# Ancho reducido (23 columnas) para que no se rompa en terminales angostas (SSH desde el celular).
echo
echo -e "\e[1;38;5;141m#####\e[0m \e[1;38;5;135m#   #\e[0m \e[1;38;5;170m#   #\e[0m \e[1;38;5;205m ### \e[0m"
echo -e "\e[1;38;5;141m    #\e[0m \e[1;38;5;135m#   #\e[0m \e[1;38;5;170m## ##\e[0m \e[1;38;5;205m#   #\e[0m"
echo -e "\e[1;38;5;141m   # \e[0m \e[1;38;5;135m#   #\e[0m \e[1;38;5;170m# # #\e[0m \e[1;38;5;205m#   #\e[0m"
echo -e "\e[1;38;5;141m  #  \e[0m \e[1;38;5;135m#   #\e[0m \e[1;38;5;170m#   #\e[0m \e[1;38;5;205m#   #\e[0m"
echo -e "\e[1;38;5;209m #   \e[0m \e[1;38;5;208m#   #\e[0m \e[1;38;5;214m#   #\e[0m \e[1;38;5;214m#   #\e[0m"
echo -e "\e[1;38;5;209m#    \e[0m \e[1;38;5;208m#   #\e[0m \e[1;38;5;214m#   #\e[0m \e[1;38;5;214m#   #\e[0m"
echo -e "\e[1;38;5;209m#####\e[0m \e[1;38;5;208m ### \e[0m \e[1;38;5;214m#   #\e[0m \e[1;38;5;214m ### \e[0m"
echo -e "\e[38;5;214m  Conectándote al mundo\e[0m"
echo
