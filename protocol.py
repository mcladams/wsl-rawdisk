from enum import IntEnum

class Command(IntEnum):
    OPEN = 1
    READ = 2
    WRITE = 3
    GET_SIZE = 4
    GET_DISKDRIVES = 5
    CLOSE = 6