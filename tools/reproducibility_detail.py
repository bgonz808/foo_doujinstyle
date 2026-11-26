"""Read-only diagnostic fingerprints. Never publish raw debug records or paths."""
from hashlib import sha256
import struct
from tools.symbol_identity import MSF7, require, u32


def hash_bytes(data):
    return sha256(data).hexdigest()


def archive_detail(data):
    """COFF archive physical member order, header and payload identities."""
    require(data[:8] == b"!<arch>\n", "Invalid COFF archive")
    at = 8
    members = []
    while at < len(data):
        require(len(members) < 100000 and at+60 <= len(data), "Invalid archive header")
        h = data[at:at+60]
        require(h[58:60] == b"`\n", "Invalid archive member trailer")
        size_text = h[48:58].strip()
        require(size_text.isdigit(), "Invalid archive member size")
        size = int(size_text)
        end = at+60+size
        require(end <= len(data), "Truncated archive member")
        stamp = h[16:28].strip()
        require(not stamp or stamp.isdigit(), "Invalid archive timestamp")
        name = h[:16].rstrip()
        payload = data[at+60:end]
        members.append({"name_sha256": hash_bytes(name),
                        "kind": "index" if name == b"/" else "names" if name == b"//" else "object",
                        "timestamp": int(stamp or b"0"),
                        "header_sha256": hash_bytes(h),
                        "size": size, "payload_sha256": hash_bytes(payload)})
        if size % 2:
            require(end < len(data) and data[end:end+1] == b"\n", "Invalid archive padding")
        at = end + size % 2
    require(at == len(data) and members, "Empty or incomplete archive")
    return members


def pdb_detail(data):
    """Hash each logical MSF stream separately from its physical block map."""
    require(len(data)>=56 and data[:32]==MSF7, "Unsupported PDB")
    block, _, blocks, directory_bytes, _, blockmap = struct.unpack_from("<IIIIII",data,32)
    require(block in (512,1024,2048,4096,8192,16384,32768)
            and blocks*block == len(data) and 0<directory_bytes<=len(data), "Invalid MSF bounds")
    count=(directory_bytes+block-1)//block
    require(blockmap<blocks and count*4<=block,"Unsupported MSF directory map")
    def read(indices, size):
        require(len(set(indices))==len(indices) and all(i<blocks for i in indices),"Invalid stream blocks")
        return b"".join(data[i*block:(i+1)*block] for i in indices)[:size]
    directory=read([u32(data,blockmap*block+4*i) for i in range(count)],directory_bytes)
    streams=u32(directory,0)
    require(2<=streams<=(len(directory)-4)//4 and streams<=100000,"Invalid stream count")
    sizes=[u32(directory,4+4*i) for i in range(streams)]
    cursor=4+4*streams
    rows=[]
    for i,size in enumerate(sizes):
        if size==0xffffffff:
            rows.append({"stream":i,"absent":True})
            continue
        n=(size+block-1)//block
        require(n<=(len(directory)-cursor)//4,"Truncated stream map")
        indices=[u32(directory,cursor+4*j) for j in range(n)]
        logical=read(indices,size)
        row={"stream":i,"size":size,"sha256":hash_bytes(logical)}
        if i==1:
            require(size>=28,"Missing PDB info")
            row["info_signature"]=u32(logical,4)
        rows.append(row)
        cursor+=4*n
    require(cursor==len(directory),"Unexpected directory data")
    return {"block_size":block,"blocks":blocks,
            "directory_sha256":hash_bytes(directory),"streams":rows}
