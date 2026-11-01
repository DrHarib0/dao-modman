dao-modman for Windows
======================

A mod manager for Dragon Age: Origins that runs in the terminal.
Documentation: https://github.com/DrHarib0/dao-modman


Starting
--------

Put dao-modman.exe wherever you like (e.g. in a "dao-modman" folder in Documents) and
double-click it. It opens in Windows Terminal.

The first start needs an internet connection: dao-modman.exe downloads Python and the
newest dao-modman and installs them in a folder of its own. That takes a minute; later
starts are quick. A setup wizard then finds the game and asks for your Nexus Mods API key.

Windows may warn about an "unrecognized app", because the exe is not signed:
click "More info", then "Run anyway".


Updating
--------

dao-modman says when a new version is out. Tools (key t) -> "Check for a new version"
updates it: dao-modman closes and the update runs in a new window.

Or, in a terminal in the folder of dao-modman.exe:

    .\dao-modman.exe self update


Uninstalling
------------

1. In dao-modman, open the settings (key s) and stop it handling nxm:// links, if you
   registered it for "Mod Manager Download" links.
2. Unapply your mods (key x), if you want the game without them.
3. In a terminal in the folder of dao-modman.exe:

       .\dao-modman.exe self remove

   Then delete dao-modman.exe.

Your mod library and settings stay, in case you come back:
    %LOCALAPPDATA%\dao-modman   (the library and downloads)
    %APPDATA%\dao-modman        (settings and the API key)
Delete these two folders to remove everything.
