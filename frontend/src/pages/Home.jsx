import { Mail, MapPin, Phone, Wifi, Zap, CreditCard, ShieldCheck, Users, BarChart3, Globe, ChevronRight } from 'lucide-react';
import { useEffect, useState, useRef } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import aboutImageUrl from '../assets/bg.jpg';

const publicApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
});

const defaultSite = {
  brand_name: 'Expressnetbilling',
  headline: 'Internet billing built for hotspot businesses',
  subheadline: 'Sell packages, collect Paystack payments, and activate MikroTik users automatically.',
  about: 'We help hotspot operators manage customers, packages, payments, and access control from one secure platform.',
  phone: '+254 700 000 000',
  email: 'support@example.com',
  location: 'Nairobi, Kenya',
  address: 'Nairobi, Kenya',
  cta_label: 'Register your business',
  cta_url: '/register',
};

// --- ANIMATION COMPONENT ---
const RevealOnScroll = ({ children, delay = 0, className = "" }) => {
  const [isVisible, setIsVisible] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          observer.unobserve(entry.target);
        }
      },
      { threshold: 0.1 }
    );

    if (ref.current) {
      observer.observe(ref.current);
    }

    return () => {
      if (ref.current) observer.unobserve(ref.current);
    };
  }, []);

  return (
    <div
      ref={ref}
      className={`transition-all duration-1000 ease-out transform ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-16'} ${className}`}
      style={{ transitionDelay: `${delay}ms` }}
    >
      {children}
    </div>
  );
};

// --- Contact Item Component ---
const ContactItem = ({ icon: Icon, title, text }) => (
  <div className="bg-white p-6 rounded-2xl shadow-sm border border-slate-100">
    <div className="flex items-center gap-4">
      <div className="w-12 h-12 rounded-full bg-blue-50 flex items-center justify-center text-[#0db5f7] text-xl shrink-0">
        <Icon size={22} />
      </div>
      <div>
        <h3 className="text-sm font-bold text-slate-400 uppercase tracking-wide mb-1">{title}</h3>
        <p className="text-slate-700 font-medium">{text}</p>
      </div>
    </div>
  </div>
);

export default function Home() {
  const [site, setSite] = useState(defaultSite);
  const [publicStats, setPublicStats] = useState(null);
  const [isTextVisible, setIsTextVisible] = useState(true);
  const [currentSlide, setCurrentSlide] = useState(0);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  useEffect(() => {
    publicApi.get('/public/site')
      .then(({ data }) => setSite(data))
      .catch(() => setSite(defaultSite));

    publicApi.get('/public/stats')
      .then(({ data }) => setPublicStats(data))
      .catch(() => setPublicStats(null));
  }, []);

  const heroSlides = [
    {
      heading: site?.headline || 'Internet billing built for hotspot businesses',
      subtext: site?.subheadline || 'Sell packages, collect Paystack payments, and activate MikroTik users automatically.',
    },
    {
      heading: 'Automate Your Hotspot, Grow Your Revenue',
      subtext: 'Seamless MikroTik integration with real-time user management and instant payment activation.',
    },
    {
      heading: 'One Platform for All Your Billing Needs',
      subtext: 'From package creation to payment collection and user control — everything in one place.',
    },
  ];

  // Hero Slider Logic
  useEffect(() => {
    const interval = setInterval(() => {
      setIsTextVisible(false);
      setTimeout(() => {
        setCurrentSlide((prev) => (prev + 1) % heroSlides.length);
        setIsTextVisible(true);
      }, 1000);
    }, 8000);

    return () => clearInterval(interval);
  }, [heroSlides.length]);

  const features = [
    {
      title: 'Package Management',
      desc: 'Create and manage data packages with flexible pricing, durations, and bandwidth limits tailored to your customers.',
      icon: Zap,
    },
    {
      title: 'Paystack Payments',
      desc: 'Accept secure payments via Paystack — mobile money, cards, and bank transfers — all seamlessly integrated.',
      icon: CreditCard,
    },
    {
      title: 'MikroTik Integration',
      desc: 'Automatically activate and deactivate hotspot users on your MikroTik router through our API integration.',
      icon: ShieldCheck,
    },
    {
      title: 'Customer Management',
      desc: 'Track customer activity, view purchase history, manage accounts, and monitor data usage in real time.',
      icon: Users,
    },
    {
      title: 'Analytics & Reports',
      desc: 'Get insights into revenue trends, peak usage hours, and customer growth with built-in dashboards.',
      icon: BarChart3,
    },
    {
      title: 'Multi-Location Support',
      desc: 'Manage multiple hotspot locations from a single dashboard with centralized billing and user control.',
      icon: Globe,
    },
  ];

  const formatStatValue = (value) => Number(value || 0).toLocaleString();
  const stats = [
    { value: formatStatValue(publicStats?.activeTenants), label: 'Active Hotspots' },
    { value: formatStatValue(publicStats?.totalCustomers), label: 'Customers Served' },
    { value: formatStatValue(publicStats?.activeCustomers), label: 'Active Customers' },
    { value: '24/7', label: 'Support' },
  ];

  return (
    <div className="font-sans text-slate-600 bg-white selection:bg-[#0db5f7] selection:text-white">
      {/* --- CSS --- */}
      <style>
        {`
          html { scroll-behavior: smooth; scroll-padding-top: 100px; }
          .no-scrollbar::-webkit-scrollbar { display: none; }
          .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }

          @keyframes float {
            0% { transform: translateY(0px); }
            50% { transform: translateY(-5px); }
            100% { transform: translateY(0px); }
          }
          .animate-float {
            animation: float 3s ease-in-out infinite;
          }

          @keyframes pulse-glow {
            0% { box-shadow: 0 0 0 0 rgba(13, 181, 247, 0.7); }
            70% { box-shadow: 0 0 0 10px rgba(13, 181, 247, 0); }
            100% { box-shadow: 0 0 0 0 rgba(13, 181, 247, 0); }
          }
          .animate-pulse-glow:hover {
            animation: pulse-glow 1.5s infinite;
          }

          @keyframes fade-in-up {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
          }
          .animate-fade-in-up {
            animation: fade-in-up 0.3s ease-out forwards;
          }
        `}
      </style>

      {/* --- HEADER --- */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-white/95 backdrop-blur-sm shadow-sm transition-all duration-300">
        <div className="mx-auto max-w-6xl px-4 h-20 flex items-center justify-between">
          <a href="#hero" className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-md bg-[#0db5f7] text-white">
              <Wifi size={22} />
            </div>
            <h1 className="text-xl font-bold text-[#0db5f7] tracking-tight">{site.brand_name}</h1>
          </a>

          <div className="hidden md:flex items-center gap-4">
            <nav className="mr-6">
              <ul className="flex space-x-8">
                {['Home', 'About', 'Features', 'Contact'].map((item) => (
                  <li key={item}>
                    <a
                      href={`#${item.toLowerCase()}`}
                      className="text-[#0db5f7] hover:text-slate-900 transition-colors font-medium text-sm uppercase tracking-wide relative group"
                    >
                      {item}
                      <span className="absolute -bottom-1 left-0 w-0 h-0.5 bg-slate-900 transition-all group-hover:w-full"></span>
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
            <Link
              to="/login"
              className="inline-flex items-center border border-[#0db5f7] text-[#0db5f7] hover:bg-[#0db5f7] hover:text-white px-5 py-2.5 rounded-full transition-all font-medium"
            >
              Login
            </Link>
            <Link
              to={site.cta_url || '/register'}
              className="inline-flex items-center bg-[#0db5f7] hover:bg-[#0ba3e0] text-white px-5 py-2.5 rounded-full transition-all shadow-lg shadow-[#0db5f7]/30 hover:shadow-[#0db5f7]/50 font-medium"
            >
              {site.cta_label || 'Register'}
            </Link>
          </div>

          <button
            className="md:hidden text-2xl text-slate-800 focus:outline-none p-2"
            onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
          >
            {isMobileMenuOpen ? '✕' : '☰'}
          </button>
        </div>

        {isMobileMenuOpen && (
          <div className="md:hidden bg-white border-t border-gray-100 py-4 px-4 shadow-xl absolute w-full animate-fade-in-up">
            <ul className="space-y-4">
              {['Home', 'About', 'Features', 'Contact'].map((item) => (
                <li key={item}>
                  <a
                    href={`#${item.toLowerCase()}`}
                    className="block text-gray-600 hover:text-[#0db5f7] font-medium text-lg py-2"
                    onClick={() => setIsMobileMenuOpen(false)}
                  >
                    {item}
                  </a>
                </li>
              ))}
              <li className="flex gap-3 pt-2">
                <Link to="/login" className="flex-1 text-center border border-[#0db5f7] text-[#0db5f7] py-3 rounded-xl font-bold" onClick={() => setIsMobileMenuOpen(false)}>
                  Login
                </Link>
                <Link to={site.cta_url || '/register'} className="flex-1 text-center bg-[#0db5f7] text-white py-3 rounded-xl font-bold shadow-md" onClick={() => setIsMobileMenuOpen(false)}>
                  {site.cta_label || 'Register'}
                </Link>
              </li>
            </ul>
          </div>
        )}
      </header>

      <main className="pt-20">
        {/* --- HERO SECTION --- */}
        <section id="hero" className="relative min-h-[90vh] flex items-center justify-center overflow-hidden">
          <div className="absolute inset-0 z-0">
            <img
              src={aboutImageUrl}
              alt=""
              className="h-full w-full object-cover"
              aria-hidden="true"
            />
            {/* Decorative pattern overlay */}
            <div className="absolute inset-0 opacity-[0.04] pointer-events-none" style={{ backgroundImage: 'radial-gradient(#fff 1px, transparent 1px)', backgroundSize: '40px 40px' }}></div>
            <div className="absolute inset-0 bg-gradient-to-r from-slate-950/90 via-slate-900/70 to-slate-900/20 z-10"></div>
          </div>

          <div className="mx-auto max-w-6xl px-4 relative z-20 text-left w-full">
            <div className="max-w-3xl">
              <div className={`transition-all duration-1000 transform ${isTextVisible ? 'translate-y-0 opacity-100' : 'translate-y-4 opacity-0'}`}>
                <span className="inline-block py-1 px-3 rounded-full bg-[#0db5f7]/20 border border-[#0db5f7]/30 text-[#0db5f7] text-sm font-bold tracking-wider mb-6">
                  HOTSPOT BILLING PLATFORM
                </span>
                <h1
                  className="text-4xl md:text-6xl lg:text-7xl font-bold text-white mb-6 leading-tight"
                >
                  {heroSlides[currentSlide].heading}
                </h1>
                <p className="text-lg md:text-xl text-gray-300 mb-10 leading-relaxed font-light max-w-2xl border-l-4 border-[#0db5f7] pl-6">
                  {heroSlides[currentSlide].subtext}
                </p>
              </div>

              {/* Slide Indicators */}
              <div className="flex gap-2 mb-8">
                {heroSlides.map((_, idx) => (
                  <button
                    key={idx}
                    className={`h-1.5 rounded-full transition-all duration-300 ${idx === currentSlide ? 'bg-[#0db5f7] w-8' : 'bg-white/30 w-2'}`}
                    onClick={() => {
                      setIsTextVisible(false);
                      setTimeout(() => {
                        setCurrentSlide(idx);
                        setIsTextVisible(true);
                      }, 300);
                    }}
                    aria-label={`Go to slide ${idx + 1}`}
                  ></button>
                ))}
              </div>

              <div className="mt-8 flex flex-wrap gap-4">
                <Link
                  to={site.cta_url || '/register'}
                  className="inline-flex items-center bg-[#0db5f7] hover:bg-[#0ba3e0] text-white font-bold py-4 px-8 rounded-full transition-all transform hover:-translate-y-1 hover:shadow-[0_10px_20px_rgba(13,181,247,0.3)] animate-pulse-glow"
                >
                  {site.cta_label || 'Register your business'}
                  <ChevronRight size={20} className="ml-2" />
                </Link>
                <a
                  href="#about"
                  className="inline-flex items-center bg-transparent border-2 border-white text-white hover:bg-white hover:text-slate-900 font-bold py-4 px-8 rounded-full transition-all"
                >
                  Learn More
                </a>
              </div>
            </div>
          </div>
        </section>

        {/* --- ABOUT SECTION --- */}
        <RevealOnScroll>
          <section id="about" className="py-24 bg-white relative">
            <div className="absolute top-0 right-0 w-1/3 h-full bg-slate-50 -z-10 hidden lg:block rounded-l-[100px]"></div>
            <div className="mx-auto max-w-6xl px-4">
              <div className="flex flex-col lg:flex-row items-center gap-16">
                <div className="w-full lg:w-1/2 relative group">
                  <div className="absolute -inset-4 bg-[#0db5f7]/20 rounded-[2rem] rotate-3 transition-transform group-hover:rotate-6"></div>
                  <div className="rounded-2xl shadow-2xl w-full h-[500px] relative z-10 overflow-hidden bg-slate-100">
                    <img
                      src={aboutImageUrl}
                      alt="Expressnetbilling platform preview"
                      className="block h-full w-full object-cover"
                    />
                  </div>
                </div>
                <div className="w-full lg:w-1/2">
                  <RevealOnScroll delay={200}>
                    <h2 className="text-4xl font-bold mb-6 text-slate-900">About Us</h2>
                    <div className="w-20 h-1.5 bg-[#0db5f7] mb-8 rounded-full"></div>
                    <p className="text-lg leading-relaxed text-gray-600 mb-4">
                      {site.about}
                    </p>
                    <p className="text-lg leading-relaxed text-gray-600 mb-8">
                      Whether you run a single hotspot or manage dozens of locations, our platform scales with your business. Automate user activation, streamline payments, and keep your customers connected — all from one secure dashboard.
                    </p>
                    <div className="flex flex-wrap gap-4">
                      <Link
                        to={site.cta_url || '/register'}
                        className="inline-flex items-center bg-[#0db5f7] hover:bg-[#0ba3e0] text-white font-bold py-3 px-8 rounded-full transition-all shadow-lg shadow-[#0db5f7]/30 hover:shadow-[#0db5f7]/50"
                      >
                        Get Started <ChevronRight size={18} className="ml-2" />
                      </Link>
                    </div>
                  </RevealOnScroll>
                </div>
              </div>
            </div>
          </section>
        </RevealOnScroll>

        {/* --- FEATURES SECTION --- */}
        <RevealOnScroll>
          <section id="features" className="py-24 bg-slate-50 relative overflow-hidden">
            <div className="absolute top-0 left-0 w-full h-full opacity-[0.03] pointer-events-none" style={{ backgroundImage: 'radial-gradient(#000 1px, transparent 1px)', backgroundSize: '30px 30px' }}></div>

            <div className="mx-auto max-w-6xl px-4 relative z-10">
              <RevealOnScroll delay={100}>
                <div className="text-center mb-16 max-w-2xl mx-auto">
                  <span className="text-[#0db5f7] font-bold tracking-widest uppercase text-sm mb-2 block">Platform Features</span>
                  <h2 className="text-4xl font-bold text-slate-900 mb-4">Everything You Need to Run Your Hotspot</h2>
                  <p className="text-slate-500 text-lg">Powerful tools designed specifically for internet service providers and hotspot operators.</p>
                </div>
              </RevealOnScroll>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                {features.map((feature, idx) => {
                  const Icon = feature.icon;
                  return (
                    <RevealOnScroll key={idx} delay={idx * 200 + 300}>
                      <div className="group bg-white rounded-3xl shadow-lg hover:shadow-[0_20px_40px_-15px_rgba(0,0,0,0.1)] transition-all duration-500 overflow-hidden flex flex-col h-full border border-slate-100 p-10 relative">
                        {/* Floating badge */}
                        <div className="absolute top-6 right-6 w-13 h-13 bg-white rounded-full shadow-md flex items-center justify-center text-[#0db5f7] text-2xl border-4 border-slate-50 transition-transform duration-500 group-hover:scale-110 group-hover:-top-8">
                          <Icon size={24} className="group-hover:animate-float" />
                        </div>

                        <h3 className="text-2xl font-bold text-slate-900 mb-3 mt-2 group-hover:text-[#0db5f7] transition-colors">{feature.title}</h3>
                        <p className="text-slate-500 leading-relaxed mb-6 flex-grow">{feature.desc}</p>

                        <a href="#features" className="inline-flex items-center text-[#0db5f7] font-bold hover:gap-2 transition-all animate-pulse-glow rounded-full">
                          Learn More <ChevronRight size={16} className="ml-1" />
                        </a>
                      </div>
                    </RevealOnScroll>
                  );
                })}
              </div>
            </div>
          </section>
        </RevealOnScroll>

        {/* --- HOW IT WORKS SECTION --- */}
        <RevealOnScroll>
          <section className="py-24 bg-white">
            <div className="mx-auto max-w-6xl px-4">
              <RevealOnScroll delay={100}>
                <div className="text-center mb-16 max-w-2xl mx-auto">
                  <span className="text-[#0db5f7] font-bold tracking-widest uppercase text-sm mb-2 block">How It Works</span>
                  <h2 className="text-4xl font-bold text-slate-900 mb-4">Get Started in 3 Simple Steps</h2>
                  <p className="text-slate-500 text-lg">From registration to activating your first customer — it only takes minutes.</p>
                </div>
              </RevealOnScroll>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                {[
                  {
                    step: '01',
                    title: 'Register Your Business',
                    desc: 'Create your account and set up your hotspot business profile in seconds. No technical skills required.',
                  },
                  {
                    step: '02',
                    title: 'Connect Your MikroTik',
                    desc: 'Link your MikroTik router to our platform using the API. We handle the rest — from user management to access control.',
                  },
                  {
                    step: '03',
                    title: 'Start Earning',
                    desc: 'Create data packages, share your payment link, and watch your revenue grow. Payments are collected automatically via Paystack.',
                  },
                ].map((item, idx) => (
                  <RevealOnScroll key={idx} delay={idx * 200 + 200}>
                    <div className="relative bg-slate-50 rounded-3xl p-10 text-center group hover:bg-[#0db5f7] transition-all duration-500 border border-slate-100 hover:border-[#0db5f7]">
                      <span className="inline-block text-6xl font-extrabold text-[#0db5f7] opacity-20 group-hover:text-white group-hover:opacity-30 transition-colors mb-4">{item.step}</span>
                      <h3 className="text-xl font-bold text-slate-900 group-hover:text-white mb-3 transition-colors">{item.title}</h3>
                      <p className="text-slate-500 group-hover:text-white/80 leading-relaxed transition-colors">{item.desc}</p>
                    </div>
                  </RevealOnScroll>
                ))}
              </div>

              <RevealOnScroll delay={600}>
                <div className="text-center mt-16">
                  <Link
                    to={site.cta_url || '/register'}
                    className="inline-flex items-center bg-[#0db5f7] hover:bg-[#0ba3e0] text-white font-bold py-4 px-10 rounded-full transition-all transform hover:-translate-y-1 hover:shadow-[0_10px_20px_rgba(13,181,247,0.3)] animate-pulse-glow text-lg"
                  >
                    {site.cta_label || 'Register your business'}
                    <ChevronRight size={22} className="ml-2" />
                  </Link>
                </div>
              </RevealOnScroll>
            </div>
          </section>
        </RevealOnScroll>

        {/* --- STATS / SOCIAL PROOF BANNER --- */}
        {/* <RevealOnScroll>
          <section className="py-20 bg-gradient-to-r from-[#0f172a] to-[#0c4a6e] text-white relative overflow-hidden">
            <div className="absolute top-0 left-0 w-96 h-96 bg-[#0db5f7] rounded-full blur-[150px] opacity-20 -translate-x-1/2 -translate-y-1/2"></div>
            <div className="absolute bottom-0 right-0 w-96 h-96 bg-purple-600 rounded-full blur-[150px] opacity-20 translate-x-1/2 translate-y-1/2"></div>

            <div className="mx-auto max-w-6xl px-4 relative z-10">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
                {stats.map((stat, idx) => (
                  <RevealOnScroll key={idx} delay={idx * 100 + 100}>
                    <div>
                      <p className="text-4xl md:text-5xl font-extrabold text-[#0db5f7] mb-2">{stat.value}</p>
                      <p className="text-slate-300 font-medium text-sm uppercase tracking-wider">{stat.label}</p>
                    </div>
                  </RevealOnScroll>
                ))}
              </div>
            </div>
          </section>
        </RevealOnScroll> */}

        {/* --- CONTACT SECTION --- */}
        <RevealOnScroll>
          <section id="contact" className="py-24 bg-white">
            <div className="mx-auto max-w-6xl px-4">
              <div className="flex flex-col lg:flex-row gap-16">

                {/* Info Column */}
                <div className="w-full lg:w-1/2 space-y-12">
                  <RevealOnScroll delay={100}>
                    <div>
                      <span className="text-[#0db5f7] font-bold tracking-widest uppercase text-sm mb-2 block">Get In Touch</span>
                      <h2 className="text-4xl font-bold text-slate-900 mb-4">Contact Us</h2>
                      <p className="text-slate-500 text-lg leading-relaxed">
                        Have questions or need support? Reach out to us and our team will get back to you as soon as possible.
                      </p>
                    </div>
                  </RevealOnScroll>

                  <div className="h-[300px] w-full rounded-3xl overflow-hidden shadow-xl bg-slate-100 relative">
                    <div className="absolute inset-0 flex items-center justify-center z-0 pointer-events-none">
                      <MapPin size={40} className="text-slate-300 animate-pulse" />
                    </div>
                    <div className="absolute inset-0 bg-gradient-to-br from-slate-200 to-slate-100 flex items-center justify-center z-10">
                      <div className="text-center">
                        <MapPin size={48} className="text-[#0db5f7] mx-auto mb-3" />
                        <p className="text-slate-600 font-bold text-lg">{site.location || site.address}</p>
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-col gap-4">
                    <ContactItem icon={Phone} title="Call Us" text={site.phone} />
                    <ContactItem icon={Mail} title="Email Us" text={site.email} />
                    <ContactItem icon={MapPin} title="Visit Us" text={site.location || site.address} />
                  </div>
                </div>

                {/* CTA Card Column */}
                <div className="w-full lg:w-1/2">
                  <div className="bg-gradient-to-br from-[#0f172a] to-[#0c4a6e] p-8 lg:p-12 rounded-3xl shadow-2xl relative overflow-hidden text-white">
                    {/* Decorative blurs */}
                    <div className="absolute top-0 right-0 w-64 h-64 bg-[#0db5f7] rounded-full blur-[100px] opacity-20 -translate-y-1/2 translate-x-1/2 pointer-events-none"></div>
                    <div className="absolute bottom-0 left-0 w-32 h-32 bg-cyan-400 rounded-full blur-[80px] translate-y-1/2 -translate-x-1/2 pointer-events-none"></div>

                    <div className="relative z-10">
                      <div className="w-16 h-16 bg-white/10 rounded-2xl flex items-center justify-center mb-8 backdrop-blur-sm border border-white/10">
                        <Wifi size={32} className="text-[#0db5f7]" />
                      </div>

                      <h3 className="text-3xl font-bold mb-4">Ready to Grow Your Hotspot Business?</h3>
                      <p className="text-slate-300 leading-relaxed mb-8 text-lg">
                        Join hundreds of hotspot operators who trust our platform to manage their billing, payments, and user activation. Get started in minutes.
                      </p>

                      <div className="space-y-4 mb-10">
                        {[
                          'Automated MikroTik user activation',
                          'Secure Paystack payment collection',
                          'Real-time analytics dashboard',
                          'Multi-location support',
                        ].map((item, idx) => (
                          <div key={idx} className="flex items-center gap-3">
                            <div className="w-6 h-6 rounded-full bg-[#0db5f7] flex items-center justify-center flex-shrink-0">
                              <ChevronRight size={14} className="text-white" />
                            </div>
                            <span className="text-slate-200">{item}</span>
                          </div>
                        ))}
                      </div>

                      <Link
                        to={site.cta_url || '/register'}
                        className="inline-flex items-center bg-[#0db5f7] hover:bg-[#0ba3e0] text-white font-bold py-4 px-10 rounded-full transition-all shadow-lg shadow-[#0db5f7]/30 hover:shadow-[#0db5f7]/50 transform hover:-translate-y-0.5 active:translate-y-0 text-lg w-full justify-center"
                      >
                        <span>{site.cta_label || 'Register your business'}</span>
                        <ChevronRight size={20} className="ml-2" />
                      </Link>

                      <p className="text-center text-slate-400 text-sm mt-4">
                        Already have an account?{' '}
                        <Link to="/login" className="text-[#0db5f7] hover:underline font-medium">
                          Login here
                        </Link>
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </RevealOnScroll>
      </main>

      {/* --- FOOTER --- */}
      <footer className="bg-[#0f172a] text-slate-300 pt-20 pb-8 font-sans">
        <div className="mx-auto max-w-6xl px-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-12 mb-16">
            {/* Brand Column */}
            <div className="space-y-6">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 bg-white/10 rounded-full flex items-center justify-center backdrop-blur-sm border border-white/10">
                  <Wifi size={24} className="text-[#0db5f7]" />
                </div>
                <span className="text-2xl font-bold text-white tracking-tight">{site.brand_name}</span>
              </div>
              <p className="text-slate-400 leading-relaxed text-sm">
                The all-in-one billing platform for hotspot operators. Manage customers, sell packages, collect payments, and control access from anywhere.
              </p>
            </div>

            {/* Company Links */}
            <div>
              <h4 className="text-white font-bold text-lg mb-6 relative inline-block">
                Company
                <span className="absolute -bottom-2 left-0 w-1/2 h-0.5 bg-[#0db5f7]"></span>
              </h4>
              <ul className="space-y-4">
                <li><a href="#hero" className="text-slate-400 hover:text-[#0db5f7] transition-colors">Home</a></li>
                <li><a href="#about" className="text-slate-400 hover:text-[#0db5f7] transition-colors">About</a></li>
                <li><a href="#features" className="text-slate-400 hover:text-[#0db5f7] transition-colors">Features</a></li>
                <li><a href="#contact" className="text-slate-400 hover:text-[#0db5f7] transition-colors">Contact</a></li>
              </ul>
            </div>

            {/* Features Links */}
            <div>
              <h4 className="text-white font-bold text-lg mb-6 relative inline-block">
                Features
                <span className="absolute -bottom-2 left-0 w-1/2 h-0.5 bg-[#0db5f7]"></span>
              </h4>
              <ul className="space-y-4">
                {features.map((feature) => (
                  <li key={feature.title}>
                    <a href="#features" className="text-slate-400 hover:text-[#0db5f7] transition-colors flex items-center gap-2 group">
                      {feature.title}
                    </a>
                  </li>
                ))}
              </ul>
            </div>

            {/* Contacts */}
            <div>
              <h4 className="text-white font-bold text-lg mb-6 relative inline-block">
                Contacts
                <span className="absolute -bottom-2 left-0 w-1/2 h-0.5 bg-[#0db5f7]"></span>
              </h4>

              <ul className="space-y-3 text-sm text-slate-400">
                <li className="flex items-center gap-3">
                  <MapPin size={16} className="text-[#0db5f7] flex-shrink-0" />
                  {site.location || site.address}
                </li>
                <li className="flex items-center gap-3">
                  <Phone size={16} className="text-[#0db5f7] flex-shrink-0" />
                  {site.phone}
                </li>
                <li className="flex items-center gap-3">
                  <Mail size={16} className="text-[#0db5f7] flex-shrink-0" />
                  {site.email}
                </li>
              </ul>
            </div>
          </div>

          <div className="border-t border-slate-800 pt-8 flex flex-col md:flex-row justify-center items-center gap-4 text-sm text-slate-500">
            <p>
              &copy; {new Date().getFullYear()}{' '}
              <span className="text-[#0db5f7] font-semibold">{site.brand_name}</span>. All Rights Reserved.
            </p>
          </div>
        </div>
      </footer>

      {/* --- SCROLL TO TOP --- */}
      <a
        href="#hero"
        className="fixed bottom-8 right-8 w-14 h-14 bg-white text-[#0db5f7] rounded-full shadow-2xl flex items-center justify-center text-2xl z-40 hover:-translate-y-2 hover:shadow-[#0db5f7]/30 transition-all duration-300 border border-slate-100"
        style={{ display: 'var(--scroll-display, none)', opacity: 'var(--scroll-opacity, 0)' }}
        onClick={(e) => {
          e.preventDefault();
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }}
      >
        ↑
      </a>
    </div>
  );
}
