import { LinkItem } from './Common';

export default function RightPanel({ rightPanelContent }) {
  return (
    <aside className="w-96 bg-white dark:bg-black border border-sjsu-gold p-8 hidden xl:block shrink-0 transition-all duration-300 rounded-2xl m-4">
       {rightPanelContent === 'empty' ? (
           <div className="h-full flex flex-col justify-center text-text-secondary text-2xl font-medium leading-tight">
               <p className="mb-1 text-text-secondary/80">Generated Links of</p>
               <p className="text-sjsu-gold font-bold">Websites <span className="text-text-secondary font-medium">and</span> Documents</p>
               <p className="text-text-secondary/80">will appear here</p>
           </div>
       ) : (
          <div className="animate-in fade-in slide-in-from-right duration-500">
               <h2 className="text-xl text-text-primary mb-8 leading-relaxed">
                  Links to <span className="font-bold text-sjsu-gold">Document</span> and <span className="font-bold text-sjsu-gold">Website</span> for this Response
               </h2>
               
               <div className="space-y-0 rounded-lg overflow-hidden border border-border-color bg-bg-main">
                   <LinkItem label="Link to website" isFirst />
                   <LinkItem label="Link to document file" />
               </div>
          </div>
       )}
    </aside>
  );
}
