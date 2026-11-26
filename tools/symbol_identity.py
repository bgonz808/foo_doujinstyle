"""Read-only native PE CodeView and MSF 7.0 PDB identity verification."""
import struct
from uuid import UUID

MSF7 = b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\x00\x00\x00"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def u32(data, offset):
    require(0 <= offset <= len(data)-4, "Truncated integer")
    return struct.unpack_from("<I", data, offset)[0]


def codeview(data, expected_name):
    require(len(data) >= 64 and data[:2] == b"MZ", "Invalid DOS header")
    pe = u32(data, 60)
    require(pe <= len(data)-24 and data[pe:pe+4] == b"PE\0\0", "Invalid PE header")
    sections = struct.unpack_from("<H", data, pe+6)[0]
    opt_size = struct.unpack_from("<H", data, pe+20)[0]
    opt = pe+24
    require(opt+opt_size+sections*40 <= len(data), "Invalid PE section table")
    require(opt_size >= 2, "Missing optional header")
    magic = struct.unpack_from("<H", data, opt)[0]
    require(magic in (0x10b, 0x20b), "Invalid optional magic")
    directories = 96 if magic == 0x10b else 112
    require(opt_size >= directories+7*8 and u32(data,opt+directories-4) >= 7,
            "Missing debug directory")
    rva, size = struct.unpack_from("<II", data, opt+directories+6*8)
    require(size >= 28 and size % 28 == 0 and size <= 28*128, "Invalid debug table")
    candidates = []
    for i in range(sections):
        at = opt+opt_size+40*i
        _, va, rawsize, raw = struct.unpack_from("<IIII", data, at+8)
        if va <= rva and rva-va <= rawsize and size <= rawsize-(rva-va):
            candidates.append(raw+rva-va)
    require(len(candidates)==1 and candidates[0]+size<=len(data), "Invalid debug RVA")
    rows = []
    for at in range(candidates[0], candidates[0]+size, 28):
        kind, length, _, ptr = struct.unpack_from("<IIII", data, at+12)
        if kind != 2:
            continue
        require(24 < length <= 4096 and ptr <= len(data)-length, "Invalid CodeView record")
        record = data[ptr:ptr+length]
        require(record[:4] == b"RSDS", "Unsupported CodeView signature")
        name, sep, _ = record[24:].partition(b"\0")
        require(sep and name == expected_name.encode("ascii"), "PE PDB reference is not expected basename")
        rows.append({"guid": str(UUID(bytes_le=bytes(record[4:20]))), "age": u32(record,20)})
    require(len(rows)==1, "Missing or ambiguous CodeView identity")
    return rows[0]


def pdb_identity(data):
    require(len(data)>=56 and data[:32]==MSF7, "Unsupported PDB container")
    block, _, blocks, directory_bytes, _, blockmap = struct.unpack_from("<IIIIII",data,32)
    require(block in (512,1024,2048,4096,8192,16384,32768) and
            blocks*block==len(data) and 0<directory_bytes<=len(data), "Invalid MSF bounds")
    count = (directory_bytes+block-1)//block
    require(blockmap<blocks and count*4<=block, "Unsupported MSF directory map")
    def read_blocks(indices, size):
        require(all(i<blocks for i in indices) and len(set(indices))==len(indices),
                "Invalid MSF stream blocks")
        return b"".join(data[i*block:(i+1)*block] for i in indices)[:size]
    directory = read_blocks([u32(data,blockmap*block+4*i) for i in range(count)],directory_bytes)
    streams = u32(directory,0)
    require(2<=streams<=(len(directory)-4)//4, "Invalid stream count")
    sizes = [u32(directory,4+4*i) for i in range(streams)]
    cursor = 4+4*streams
    for i,size in enumerate(sizes):
        if size==0xffffffff:
            require(i!=1,"Missing PDB info stream")
            continue
        n=(size+block-1)//block
        require(n<=(len(directory)-cursor)//4,"Truncated MSF stream map")
        if i==1:
            require(28<=size<=len(data),"Invalid PDB info size")
            info=read_blocks([u32(directory,cursor+4*j) for j in range(n)],size)
            return {"guid":str(UUID(bytes_le=bytes(info[12:28]))),"age":u32(info,8)}
        cursor+=4*n
    raise ValueError("Missing PDB info stream")


def verify_pair(pe, pdb, name):
    cv = codeview(pe,name)
    require(cv == pdb_identity(pdb), "PE/PDB GUID or age mismatch")
    return {**cv, "basename_verified": True}
