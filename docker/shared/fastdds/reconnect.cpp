#include <rtps/transport/TCPv4Transport.h>
#include <rtps/transport/TCPChannelResource.h>
#include <fastrtps/utils/IPLocator.h>
#include <iostream>
#include <thread>
using namespace eprosima::fastrtps::rtps;
using namespace eprosima::fastdds::rtps;
class Client : public TCPv4Transport {
public:
    explicit Client(const TCPv4TransportDescriptor& d):TCPv4Transport(d){}
    using TCPv4Transport::send;
    bool opened(const Locator_t& physical, uint16_t port) {
        std::lock_guard<std::mutex> guard(sockets_map_mutex_);
        auto it=channel_resources_.find(physical);
        return it!=channel_resources_.end() && it->second->is_logical_port_opened(port);
    }
};
int main() {
    TCPv4TransportDescriptor sd; sd.add_listener_port(11821); sd.max_logical_port=0;
    TCPv4TransportDescriptor cd; cd.max_logical_port=0;
    Client client(cd); if(!client.init()) return 2;
    Locator_t target; target.kind=LOCATOR_KIND_TCPv4;
    IPLocator::setIPv4(target,127,0,0,1); IPLocator::setPhysicalPort(target,11821); IPLocator::setLogicalPort(target,7410);
    const auto physical=IPLocator::toPhysicalLocator(target);
    SendResourceList resources;
    for(int cycle=0;cycle<5;++cycle) {
        auto server=std::unique_ptr<TCPv4Transport>(new TCPv4Transport(sd)); if(!server->init()) return 3;
        if(cycle==0 && !client.OpenOutputChannel(resources,target)) return 4;
        // Listener exists but no DDS logical input: reproduce startup rejection.
        for(int i=0;i<15;++i) {client.send(nullptr,0,physical,target);std::this_thread::sleep_for(std::chrono::milliseconds(100));}
        if(client.opened(physical,7410)) return 5;
        if(!server->OpenInputChannel(target,nullptr,65535)) return 6;
        auto start=std::chrono::steady_clock::now();
        while(!client.opened(physical,7410) && std::chrono::steady_clock::now()-start<std::chrono::seconds(3)) {
            client.send(nullptr,0,physical,target); std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        bool recovered=client.opened(physical,7410);
        std::cout<<"cycle="<<cycle<<" recovered="<<recovered<<" milliseconds="<<std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count()<<std::endl;
        server->CloseInputChannel(target);server.reset();std::this_thread::sleep_for(std::chrono::milliseconds(200));
        if(!recovered) return 1;
    }
}
