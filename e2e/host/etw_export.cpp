// Narrow test-only TraceLogging consumer. No general ETL conversion.
#include <windows.h>
#include <evntrace.h>
#include <evntcons.h>
#include <tdh.h>
#include <array>
#include <cstdio>
#include <cstring>
#include <string>
#include <stdexcept>
#include <vector>
#include <cstdint>
#include <cwchar>

namespace {
constexpr GUID provider{0xa4ab84a0,0x75db,0x4f17,{0xb7,0xe3,0xf1,0xaa,0x0d,0x26,0x0d,0x4a}};
constexpr const char* allowed[]={
 "component.track.open.begin","component.track.open.end","component.archive.member.ready",
 "component.decoder.initialize.begin","component.decoder.initialize.end","component.decoder.first_pcm",
 "component.decoder.seek.begin","component.decoder.seek.first_pcm",
 "component.ui.search.selection.changed","component.ui.search.drag.request",
 "component.ui.search.drag.no_tracks","component.ui.search.drag.has_tracks",
 "component.ui.search.drag.drop_accepted","component.ui.search.open_release",
 "component.ui.search.track.updated","component.ui.search.track.projected",
 "component.ui.search.worker.started","component.ui.idle.worker.started","component.ui.search.worker.exited"
};
struct Context { std::string run; unsigned count=0; bool failed=false; };
void require(bool ok,const char* msg) { if(!ok) throw std::runtime_error(msg); }
bool valid_id(const std::string& s) {
 if(s.size()<8 || s.size()>64) return false;
 for(char c:s) if(!((c>='A'&&c<='Z')||(c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='_'||c=='-')) return false;
 return true;
}
std::vector<BYTE> property(PEVENT_RECORD e,const wchar_t* name) {
 PROPERTY_DATA_DESCRIPTOR d{};
 d.PropertyName=reinterpret_cast<ULONG_PTR>(name); d.ArrayIndex=ULONG_MAX;
 ULONG size=0;
 ULONG result=TdhGetPropertySize(e,0,nullptr,1,&d,&size);
 if(result!=ERROR_SUCCESS) { std::fwprintf(stderr,L"TDH property size %ls: %lu\n",name,result); throw std::runtime_error("TDH property size"); }
 require(size>0&&size<=256,"Property size out of bounds");
 std::vector<BYTE> data(size);
 result=TdhGetProperty(e,0,nullptr,1,&d,size,data.data());
 require(result==ERROR_SUCCESS,"TDH property read failed");
 return data;
}
std::string str(PEVENT_RECORD e,const wchar_t* name) {
 auto p=property(e,name);
 require(p.back()==0 && std::memchr(p.data(),0,p.size()-1)==nullptr,"Invalid string encoding");
 return std::string(reinterpret_cast<const char*>(p.data()),p.size()-1);
}
template<class T> T number(PEVENT_RECORD e,const wchar_t* name) {
 auto p=property(e,name);
 require(p.size()==sizeof(T),"Numeric width mismatch");
 T value{}; std::memcpy(&value,p.data(),sizeof(value)); return value;
}
void WINAPI on_event(PEVENT_RECORD e) noexcept {
 auto& ctx=*static_cast<Context*>(e->UserContext);
 if(ctx.failed || !IsEqualGUID(e->EventHeader.ProviderId,provider)) return;
 try {
  require(ctx.count<100000,"Event count limit");
  ULONG bytes=0;
  require(TdhGetEventInformation(e,0,nullptr,nullptr,&bytes)==ERROR_INSUFFICIENT_BUFFER &&
          bytes>=sizeof(TRACE_EVENT_INFO)&&bytes<=65536,"TDH metadata size");
  std::vector<BYTE> info(bytes);
  auto* metadata=reinterpret_cast<PTRACE_EVENT_INFO>(info.data());
  require(TdhGetEventInformation(e,0,nullptr,metadata,&bytes)==ERROR_SUCCESS &&
          metadata->DecodingSource==DecodingSourceTlg &&
          metadata->TopLevelPropertyCount==9 && metadata->PropertyCount==9,"Unexpected event schema");
  const auto name=str(e,L"Event"),run=str(e,L"RunId"),scenario=str(e,L"ScenarioId"),attempt=str(e,L"AttemptId");
  bool known=false; for(const auto* s:allowed) if(name==s) known=true;
  require(known&&run==ctx.run&&valid_id(scenario)&&valid_id(attempt),"Unexpected event identity");
  const auto tick=number<std::uint64_t>(e,L"QpcTick"),freq=number<std::uint64_t>(e,L"QpcFrequency"),samples=number<std::uint64_t>(e,L"SampleCount");
  const auto pid=number<std::uint32_t>(e,L"Pid"),tid=number<std::uint32_t>(e,L"Tid");
  require(pid>0&&pid==e->EventHeader.ProcessId&&tid==e->EventHeader.ThreadId&&freq>0,"Event header or clock mismatch");
  require((name!="component.decoder.first_pcm"&&name!="component.decoder.seek.first_pcm")||samples>0,"Empty PCM milestone");
  require(std::printf("{\"schema_version\":1,\"run_id\":\"%s\",\"scenario_id\":\"%s\",\"attempt_id\":\"%s\",\"source\":\"component\",\"pid\":%u,\"seq\":%u,\"event\":\"%s\",\"clock\":{\"domain\":\"win-qpc:%s\",\"tick\":%llu,\"frequency_hz\":%llu},\"metrics\":{\"sample_count\":%llu}}\n",
      run.c_str(),scenario.c_str(),attempt.c_str(),pid,ctx.count,name.c_str(),run.c_str(),
      static_cast<unsigned long long>(tick),static_cast<unsigned long long>(freq),static_cast<unsigned long long>(samples))>0,"Output failure");
  ++ctx.count;
 } catch(const std::exception& ex) { std::fprintf(stderr,"ETW export: %s\n",ex.what()); ctx.failed=true; }
 catch(...) { ctx.failed=true; }
}
}
int wmain(int argc,wchar_t** argv) {
 if(argc!=3 || std::wcslen(argv[2])!=32) return 2;
 Context ctx;
 for(const wchar_t* p=argv[2];*p;++p) {
  if(!((*p>=L'0'&&*p<=L'9')||(*p>=L'a'&&*p<=L'f'))) return 2;
  ctx.run.push_back(static_cast<char>(*p));
 }
 EVENT_TRACE_LOGFILEW log{};
 log.LogFileName=argv[1]; log.ProcessTraceMode=PROCESS_TRACE_MODE_EVENT_RECORD;
 log.EventRecordCallback=on_event; log.Context=&ctx;
 TRACEHANDLE handle=OpenTraceW(&log);
 if(handle==INVALID_PROCESSTRACE_HANDLE) { std::fprintf(stderr,"OpenTrace: %lu\n",GetLastError()); return 1; }
 const ULONG status=ProcessTrace(&handle,1,nullptr,nullptr);
 const ULONG close=CloseTrace(handle);
 if(status!=ERROR_SUCCESS||close!=ERROR_SUCCESS||ctx.failed||ctx.count==0||
    log.EventsLost||log.LogfileHeader.BuffersLost||std::fflush(stdout)!=0) {
  std::fprintf(stderr,"ETW rejected: process=%lu close=%lu count=%u lost=%lu buffers=%lu\n",
    status,close,ctx.count,log.EventsLost,log.LogfileHeader.BuffersLost); return 1;
 }
 return 0;
}
