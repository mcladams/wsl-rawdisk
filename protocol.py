from enum import IntEnum

class Command(IntEnum):
    OPEN = 1
    READ = 2
    WRITE = 3
    GET_SIZE = 4
    GET_DISKDRIVES = 5
    CLOSE = 6

FMT_OPEN = "=BHB"
FMT_OPEN_POST_CMD = "=" + FMT_OPEN[2:]

FMT_READ_WRITE = "=BH2Q"
FMT_READ_WRITE_POST_CMD = "=" + FMT_READ_WRITE[2:]

FMT_GET_SIZE = "=BH"
FMT_GET_SIZE_POST_CMD = "=" + FMT_GET_SIZE[2:]

FMT_REPLY_BYTE = "B"
FMT_REPLY_SHORT = "h"
FMT_REPLY_QWORD = "Q"