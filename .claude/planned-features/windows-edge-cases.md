# Planned: Windows edge cases

## Why

The Windows port works for the author's Steam install, but some situations that only Windows
has were never tried. Each should end in a clear message, never in a half-applied game folder.

## Paths over 260 characters

- Windows' old path limit: the library is under `%LOCALAPPDATA%\dao-modman\mods\<id>\files\...`,
  mods have deep folders, and the game folder can be long too
  (`C:\Program Files (x86)\Steam\steamapps\common\Dragon Age Ultimate Edition\...`).
- Python handles long paths if Windows' "LongPathsEnabled" is on; otherwise copies fail.
- To do: a test with a deep mod folder; on failure an error naming the path and the Windows
  setting (or `\\?\` paths where we copy).

## Files in use

- Windows refuses to replace or delete an open file: the game running during apply/unapply, a
  file open in an editor or a viewer, an antivirus scan.
- To do: try apply/unapply/import with the game running; the error should name the file and say
  to close the game; check that `deploy` rolls back cleanly (the record of what was applied must
  match the disk).

## No write permission in `C:\Program Files`

- Steam libraries are writable for the user, but a GOG or EA install in `C:\Program Files` is
  not: `laa patch`, `laa restore`, `console enable/disable` write into the install folder.
- To do: catch `PermissionError` there and say what to do (run dao-modman as administrator
  once, or move the game out of `C:\Program Files`).
