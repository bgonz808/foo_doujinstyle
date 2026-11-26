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
        require(not stamp or stamp.isdigit() or stamp == b"-1", "Invalid archive timestamp")
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


def coff_detail(data):
    """Group raw COFF sections by family; distinguish code from debug data.

    This is a diagnostic, not semantic equivalence or a replacement for a
    complete object hash. Section order and raw names are deliberately retained
    within each family digest; relocations and symbol table are not normalized.
    """
    require(len(data) >= 20, "Truncated COFF object")
    if data[:4] == b"\0\0\xff\xff":
        require(len(data)>=56 and struct.unpack_from("<H",data,4)[0]>=2
                and data[12:28]==bytes.fromhex("c7a1bad1eebaa94baf20faf66aa4dcb8"),
                "Unsupported anonymous COFF object")
        sections, start, stamp = u32(data,44), 56, u32(data,8)
    else:
        sections = struct.unpack_from("<H",data,2)[0]
        start = 20+struct.unpack_from("<H",data,16)[0]
        stamp = u32(data,4)
    require(0<sections<=100000 and start+40*sections<=len(data), "Invalid COFF section table")
    groups = {}
    for i in range(sections):
        at=start+40*i
        name=data[at:at+8].rstrip(b"\0")
        size,ptr=struct.unpack_from("<II",data,at+16)
        require(not ptr or (ptr<=len(data) and size<=len(data)-ptr), "Invalid COFF raw data")
        family = next((x.decode("ascii") for x in (b".text",b".debug",b".rdata",b".data",b".bss",b".pdata",b".xdata",b".drectve") if name.startswith(x)), "other")
        row=groups.setdefault(family, {"sections":0,"bytes":0,"digest":sha256()})
        row["sections"]+=1
        row["bytes"]+=size if ptr else 0
        row["digest"].update(len(name).to_bytes(2,"big")+name+size.to_bytes(4,"big"))
        row["digest"].update(sha256(data[ptr:ptr+size] if ptr else b"").digest())
    return {"timestamp":stamp,"sections":sections,"families":{
        k:{"sections":v["sections"],"bytes":v["bytes"],"sha256":v["digest"].hexdigest()}
        for k,v in sorted(groups.items())}}
